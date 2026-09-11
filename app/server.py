#!/usr/bin/env python3
"""Local server for File Finder: search, preview, and open files.

Security posture (built in from the start, informed by an audit of the
sibling apps): loopback-only bind, per-launch random token on every route,
Host-header validation against DNS rebinding, Content-Length validation,
and — critically — the open/preview routes will only act on paths that are
present in the index, never on an arbitrary path supplied by the caller.
"""

import argparse
import json
import mimetypes
import os
import time
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DB_PATH = APP_DIR / "files.db"
MAX_BODY = 1 << 20
PREVIEW_BYTES = 256 * 1024
TEXT_EXT = {
    "txt","md","py","js","ts","tsx","jsx","json","yaml","yml","toml","ini","cfg",
    "conf","sh","bash","zsh","c","h","cpp","hpp","java","go","rs","rb","php","pl",
    "sql","html","htm","css","scss","xml","csv","tsv","log","env","gitignore",
    "dockerfile","makefile","rst","tex","qml","vue","svelte","lua","r","jl","kt",
}
IMAGE_EXT = {"png","jpg","jpeg","gif","webp","bmp","svg","ico","avif"}


class DB:
    def __init__(self, path):
        self.path = path
        if not path.exists():
            raise SystemExit(f"No index at {path}\nRun: python3 {APP_DIR}/index_files.py")

    def conn(self):
        c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c


class Handler(BaseHTTPRequestHandler):
    server_version = "filefinder/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------- plumbing ----------
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _host_ok(self):
        h = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return h in ("127.0.0.1", "localhost", "::1", "")

    def _authed(self, qs):
        tok = (qs.get("t") or [None])[0]
        if tok:
            try:
                if secrets.compare_digest(tok.encode("utf-8", "surrogatepass"),
                                          self.server.token.encode("utf-8")):
                    return True
            except (TypeError, UnicodeError):
                pass
        return self.server.token in (self.headers.get("Referer") or "")

    def _indexed(self, path: str):
        """Return the row for path, or None. Gate every filesystem action on this."""
        with self.server.db.conn() as c:
            return c.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()

    # ---------- routing ----------
    def do_GET(self):
        if not self._host_ok():
            return self._send(421, b"bad host", "text/plain; charset=utf-8")
        parsed = urlparse(self.path)
        path, qs = unquote(parsed.path), parse_qs(parsed.query)

        if path.startswith("/static/"):
            return self.static(path[len("/static/"):])
        if not self._authed(qs):
            return self._send(403, b"forbidden", "text/plain; charset=utf-8")
        if path == "/":
            return self.static("index.html")
        if path == "/api/stats":
            return self.api_stats()
        if path == "/api/exts":
            return self.api_exts()
        if path == "/api/search":
            return self.api_search(qs)
        if path == "/api/preview":
            return self.api_preview(qs)
        return self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._host_ok():
            return self._send(421, b"bad host", "text/plain; charset=utf-8")
        parsed = urlparse(self.path)
        path, qs = unquote(parsed.path), parse_qs(parsed.query)
        if not self._authed(qs):
            return self._send(403, b"forbidden", "text/plain; charset=utf-8")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json(400, {"error": "bad Content-Length"})
        if n < 0 or n > MAX_BODY:
            return self._json(413, {"error": "bad or oversized body"})
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        if path == "/api/reindex":
            return self.api_reindex()
        if path == "/api/open":
            return self.api_open(payload)
        return self._send(404, b"not found", "text/plain; charset=utf-8")

    # ---------- api ----------
    def api_stats(self):
        with self.server.db.conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM files").fetchone()["n"]
            size = c.execute("SELECT COALESCE(SUM(size),0) s FROM files").fetchone()["s"]
            folders = c.execute("SELECT COUNT(DISTINCT folder) n FROM files").fetchone()["n"]
        return self._json(200, {"total": total, "bytes": size, "folders": folders,
                                "home": str(Path.home())})

    def api_exts(self):
        with self.server.db.conn() as c:
            rows = c.execute(
                "SELECT ext, COUNT(*) n, COALESCE(SUM(size),0) b FROM files"
                " WHERE ext <> '' GROUP BY ext ORDER BY n DESC LIMIT 400").fetchall()
        return self._json(200, {"exts": [dict(r) for r in rows]})

    def api_search(self, qs):
        q = (qs.get("q") or [""])[0].strip()
        ext = (qs.get("ext") or [""])[0].strip().lower()
        sort = (qs.get("sort") or ["relevance"])[0]
        try:
            limit = max(1, min(1000, int((qs.get("limit") or ["200"])[0])))
        except ValueError:
            limit = 200

        order = {"name": "f.name COLLATE NOCASE ASC",
                 "size": "f.size DESC",
                 "newest": "f.mtime DESC",
                 "oldest": "f.mtime ASC"}.get(sort, "f.name COLLATE NOCASE ASC")

        where, params = [], []
        if ext:
            where.append("f.ext = ?")
            params.append(ext)

        with self.server.db.conn() as c:
            if q:
                # LIKE on an indexed column: predictable, handles partial words and
                # punctuation that an FTS tokenizer would split awkwardly.
                where.append("f.name LIKE ? ESCAPE '\\'")
                params.append("%" + q.replace("\\", "\\\\").replace("%", "\\%")
                                      .replace("_", "\\_") + "%")
            sql = "SELECT f.* FROM files f"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += f" ORDER BY {order} LIMIT ?"
            params.append(limit)
            rows = c.execute(sql, params).fetchall()
            cnt_sql = "SELECT COUNT(*) n FROM files f"
            if where:
                cnt_sql += " WHERE " + " AND ".join(where)
            total = c.execute(cnt_sql, params[:-1]).fetchone()["n"]

        out = []
        for r in rows:
            d = dict(r)
            d["kind"] = ("image" if d["ext"] in IMAGE_EXT
                         else "text" if d["ext"] in TEXT_EXT else "other")
            out.append(d)
        return self._json(200, {"results": out, "total": total, "shown": len(out)})

    def api_preview(self, qs):
        p = (qs.get("path") or [""])[0]
        row = self._indexed(p)
        if not row:
            return self._json(404, {"error": "not in index"})
        f = Path(p)
        if not f.is_file():
            return self._json(410, {"error": "file no longer on disk"})
        ext = (row["ext"] or "").lower()

        if ext in IMAGE_EXT:
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if f.stat().st_size > 12_000_000:
                return self._json(413, {"error": "image too large to preview"})
            return self._send(200, f.read_bytes(), ctype)

        if ext in TEXT_EXT or row["size"] < 2_000_000:
            try:
                data = f.open("rb").read(PREVIEW_BYTES)
            except OSError as e:
                return self._json(403, {"error": "cannot read that file"})
            if b"\x00" in data[:8000]:
                return self._json(200, {"binary": True,
                                        "note": "binary file - no text preview"})
            text = data.decode("utf-8", "replace")
            return self._json(200, {"text": text,
                                    "truncated": row["size"] > PREVIEW_BYTES,
                                    "lines": text.count("\n") + 1})
        return self._json(200, {"binary": True, "note": "no preview for this type"})

    def api_reindex(self):
        """Rebuild the file index and reopen the database in place."""
        try:
            r = subprocess.run(
                [sys.executable, str(APP_DIR / "index_files.py"), "--quiet"],
                capture_output=True, text=True, timeout=1800)
            if r.returncode != 0:
                return self._json(500, {"error": "reindex failed"})
            self.server.db = DB(DB_PATH)
        except (subprocess.TimeoutExpired, OSError) as e:
            return self._json(500, {"error": "reindex failed"})
        with self.server.db.conn() as c:
            n = c.execute("SELECT COUNT(*) n FROM files").fetchone()["n"]
        return self._json(200, {"ok": True, "total": n})

    def api_open(self, payload):
        """Open a file, or its containing folder, in the desktop's default app.

        Only paths present in the index are accepted, so a crafted request
        cannot make this launch something arbitrary.
        """
        p = payload.get("path") or ""
        what = payload.get("what") or "file"
        row = self._indexed(p)
        if not row:
            return self._json(403, {"error": "path is not in the index"})

        target = p if what == "file" else str(Path(p).parent)
        if not Path(target).exists():
            return self._json(410, {"error": "no longer on disk"})

        if what == "folder":
            fm = shutil.which("pcmanfm-qt")
            cmd = [fm, "--select", p] if fm else [shutil.which("xdg-open") or "xdg-open", target]
        else:
            cmd = [shutil.which("xdg-open") or "xdg-open", target]
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, start_new_session=True,
                             env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")})
        except OSError as e:
            return self._json(500, {"error": "could not launch the handler"})
        return self._json(200, {"ok": True, "opened": target})

    def static(self, rel):
        target = (STATIC_DIR / rel).resolve()
        if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        return self._send(200, target.read_bytes(), ctype)



def _exit_when_orphaned(httpd, interval=5.0):
    """Shut down if our launcher goes away.

    Finding 9: the launcher's EXIT trap is skipped on SIGKILL, and bash defers
    it while the browser runs in the foreground, so a server could linger with
    no window - an unmanaged endpoint holding a live token. Re-parenting to
    init is the reliable signal that our launcher is gone.
    """
    import threading

    def watch():
        start_ppid = os.getppid()
        while True:
            time.sleep(interval)
            ppid = os.getppid()
            if ppid != start_ppid and ppid == 1:
                httpd.shutdown()
                return
    t = threading.Thread(target=watch, daemon=True)
    t.start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--token", default=None)
    ap.add_argument("--print-url", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    db = DB(DB_PATH)
    port = args.port
    if not port:
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    token = args.token or secrets.token_urlsafe(18)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    httpd.db, httpd.token, httpd.verbose = db, token, args.verbose
    url = f"http://127.0.0.1:{port}/?t={token}"
    print(url if args.print_url else f"File Finder at {url}", flush=True)
    _exit_when_orphaned(httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

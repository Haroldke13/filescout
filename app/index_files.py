#!/usr/bin/env python3
"""Index every file under a root into a SQLite database with full-text search.

Scales to hundreds of thousands of files. By default it skips the directories
that hold dependency/library code rather than your own work (site-packages,
node_modules, .venv, caches); pass --all to index those too.
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
APP_DIR = HOME / ".local/share/filefinder"
DB_PATH = APP_DIR / "files.db"

# Dependency/library trees: real files, but almost never what you are looking for.
NOISE_DIRS = {
    "site-packages", "dist-packages", "node_modules", "__pycache__",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".cache", ".npm", ".yarn", ".gradle", ".m2", ".cargo", ".rustup",
    ".git", ".svn", ".hg", ".terraform", "vendor", ".next", ".nuxt",
    "bower_components", ".ipynb_checkpoints", ".pnpm-store",
}
ALWAYS_SKIP = {"/proc", "/sys", "/dev", "/run"}

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=OFF;
CREATE TABLE IF NOT EXISTS files (
  id      INTEGER PRIMARY KEY,
  path    TEXT UNIQUE NOT NULL,
  name    TEXT NOT NULL,
  folder  TEXT NOT NULL,
  ext     TEXT,
  size    INTEGER,
  mtime   INTEGER,
  is_dir  INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ext    ON files(ext);
CREATE INDEX IF NOT EXISTS idx_name   ON files(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_folder ON files(folder);
CREATE INDEX IF NOT EXISTS idx_mtime  ON files(mtime);
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts
  USING fts5(name, folder, content='files', content_rowid='id', tokenize="unicode61");
"""


def walk(root: Path, include_all: bool):
    """Yield (path, name, folder, ext, size, mtime) for every file under root."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=None):
        if any(dirpath.startswith(s) for s in ALWAYS_SKIP):
            dirnames[:] = []
            continue
        if not include_all:
            dirnames[:] = [d for d in dirnames if d not in NOISE_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not (st.st_mode & 0o170000) == 0o100000:   # regular files only
                continue
            ext = os.path.splitext(fn)[1].lower().lstrip(".")[:24]
            yield (full, fn, dirpath, ext, st.st_size, int(st.st_mtime))


def build(root: Path, db_path: Path, include_all: bool, quiet: bool):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)

    t0, n, batch = time.time(), 0, []
    for row in walk(root, include_all):
        batch.append(row)
        n += 1
        if len(batch) >= 20000:
            con.executemany(
                "INSERT OR IGNORE INTO files(path,name,folder,ext,size,mtime)"
                " VALUES (?,?,?,?,?,?)", batch)
            batch.clear()
            if not quiet:
                print(f"\r  indexed {n:,} files…", end="", file=sys.stderr, flush=True)
    if batch:
        con.executemany(
            "INSERT OR IGNORE INTO files(path,name,folder,ext,size,mtime)"
            " VALUES (?,?,?,?,?,?)", batch)

    con.execute("INSERT INTO files_fts(rowid, name, folder)"
                " SELECT id, name, folder FROM files")
    con.execute("INSERT INTO files_fts(files_fts) VALUES('optimize')")
    con.commit()
    con.execute("ANALYZE")
    con.commit()
    con.close()
    tmp.replace(db_path)
    db_path.chmod(0o600)   # the index lists every file you own

    if not quiet:
        dt = time.time() - t0
        print(f"\r  indexed {n:,} files in {dt:.1f}s -> {db_path}"
              f" ({db_path.stat().st_size/1e6:.0f} MB)      ", file=sys.stderr)
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(HOME), help="directory to index (default: $HOME)")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--all", action="store_true",
                    help="also index site-packages, node_modules, caches, .git")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not args.quiet:
        print(f"Indexing {root}"
              f"{' (including dependency trees)' if args.all else ''}…", file=sys.stderr)
    build(root, Path(args.db), args.all, args.quiet)


if __name__ == "__main__":
    main()

# File Finder

Search every file on your machine by name, filter by extension, preview it,
and open it — or the folder containing it — in one keystroke.

Built for Lubuntu/LXQt, works on any Linux desktop.

![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)

---

## Why

`find` is fine until you don't remember the name, the extension, or the
directory. This indexes your whole home directory into SQLite once, then
answers queries instantly — with a preview pane, an extension dropdown, and
a button that drops you straight into the containing folder.

**It installs nothing.** Python standard library (`sqlite3` included) plus the
browser you already have. No pip, no apt, no root.

## Features

- **Indexes everything** — 126,000 files in ~16 seconds in testing
- **Extension dropdown**, ranked by how many files you actually have of each
  type, with counts (`.docx (5,776)`, `.py (4,320)`, …)
- **Instant name search**, composable with the extension filter
- **Preview pane** — syntax-agnostic text preview for source and documents,
  inline rendering for images, honest "no preview" for binaries
- **Open file** in your default application, or **open the containing folder**
  with the file already selected
- **Copy full path** to the clipboard
- Sort by name, newest, oldest, or largest
- Keyboard driven: `/` to search, `↑`/`↓` to move, `Enter` to open,
  `Shift+Enter` to open the folder

By default it skips dependency trees — `site-packages`, `node_modules`,
`.venv`, `.git`, caches — because those are library code, not your work. That
alone cut a test index from 676,000 files to 126,000. Use `--all` if you want
them.

## Requirements

| Need | Why |
|---|---|
| Python 3.9+ | Runs the local server (`sqlite3` is in the standard library) |
| Chrome or Chromium | Renders the UI |

## Install

```bash
git clone <your-repo-url> filefinder
cd filefinder
./install.sh
```

Installs to `~/.local/share/filefinder/`, puts `filefinder` on your `PATH`,
adds a menu entry and desktop shortcut with the app icon, and builds the first
index.

## Usage

| Command | Does |
|---|---|
| `filefinder` | Launch the search window |
| `filefinder --reindex` | Rescan your home directory, then launch |
| `filefinder --all` | Rescan including `site-packages`, `node_modules`, caches |
| `filefinder --scan-only` | Rescan and exit |
| `filefinder --serve` | Run the server only; open the printed URL yourself |

Re-run `--reindex` after you've added a lot of files. The index is a snapshot,
not a live watch.

## Security

The server binds to **loopback only** and requires a fresh random token,
minted per launch, on every route. It also:

- **validates the `Host` header**, so a malicious page cannot reach it by
  pointing its own domain at `127.0.0.1` (DNS rebinding)
- **only acts on paths that are in the index** — the open and preview routes
  reject anything else, so a crafted request cannot make the app launch or
  read an arbitrary file
- validates `Content-Length` (a negative value otherwise defeats body caps)
- confines static file serving by path components, not string prefix

## Layout

```
.
├── app/
│   ├── index_files.py      # filesystem walker -> SQLite + FTS5
│   ├── server.py           # stdlib HTTP server: search, preview, open
│   ├── filefinder          # launcher
│   ├── set-window-icon.sh  # crisp taskbar icon (bash + xprop only)
│   ├── icon-wm.dat         # precomputed icon data
│   └── static/index.html   # the whole UI, one file
├── desktop/filefinder.desktop.in
├── icons/hicolor/…         # SVG + 8 PNG sizes
├── install.sh
├── uninstall.sh
└── LICENSE
```

## Uninstall

```bash
./uninstall.sh
```

Removes the app and its index. **Your files are never touched.**

## Troubleshooting

**A file I just created doesn't appear.** Run `filefinder --reindex`.

**Nothing opens when I click "Open file".** The app shells out to `xdg-open`.
Check that a default application is associated with that file type.

**The command isn't found.** Add `export PATH="$HOME/.local/bin:$PATH"` to
`~/.bashrc`.

## Licence

**Proprietary software — all rights reserved.** Copyright © 2026 Joel Harold Onyango.

This repository is not open source. The full terms are in [LICENSE](LICENSE); in
summary, you may not copy, redistribute, modify, sublicense, publish, re-host or
commercially exploit this software, in whole or in part, without the prior
written permission of the copyright holder. Access to this repository does not
grant any licence beyond reading it.

Previous versions of this repository were published under an open-source licence.
That change is not retroactive: copies obtained under the earlier licence remain
governed by its terms. Everything from this commit onward is covered by
[LICENSE](LICENSE).

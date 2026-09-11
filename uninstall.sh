#!/usr/bin/env bash
# File Finder — uninstaller. Removes the app; never touches your media files.
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/filefinder"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

echo "This removes the File Finder app from:"
echo "  $APP_DIR"
echo "  $BIN_DIR/filefinder"
echo "  $DESKTOP_DIR/filefinder.desktop"
echo
echo "your files are NOT touched."
read -rp "Proceed? [y/N] " a
[[ "$a" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 0; }

rm -rf  "$APP_DIR"
rm -f   "$BIN_DIR/filefinder"
rm -f   "$DESKTOP_DIR/filefinder.desktop"
rm -f   "$DESK/File Finder.desktop"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
find "$ICON_DIR" -name 'filefinder.png' -o -name 'filefinder.svg' 2>/dev/null | while read -r i; do rm -f "$i"; done
command -v gtk-update-icon-cache >/dev/null 2>&1 \
  && gtk-update-icon-cache -f -t "$ICON_DIR" 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 \
  && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
echo "Removed."

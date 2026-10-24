#!/bin/bash
set -e
REPO="https://github.com/yassine-aitmohamed/vanoatlas"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SRC" ] && [ -f "$SRC/VanoAtlas/vanoatlas.py" ]; then
  DIR="$SRC"
else
  DIR="${VANOATLAS_DIR:-$HOME/VanoAtlas}"
  echo "Downloading VanoAtlas to $DIR ..."
  TMP="$(mktemp -d)"
  curl -fsSL "$REPO/archive/refs/heads/main.zip" -o "$TMP/vanoatlas.zip"
  unzip -q "$TMP/vanoatlas.zip" -d "$TMP"
  mkdir -p "$DIR"
  cp -R "$TMP"/vanoatlas-main/. "$DIR"/
  rm -rf "$TMP"
fi
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "Python 3 is required. On macOS: xcode-select --install  (or https://www.python.org/downloads/)"
  exit 1
fi
echo "Setting up VanoLeaf ..."
"$PY" -m venv "$DIR/VanoLeaf/.venv"
"$DIR/VanoLeaf/.venv/bin/python3" -m pip install -q --upgrade pip || true
"$DIR/VanoLeaf/.venv/bin/python3" -m pip install -q -r "$DIR/VanoLeaf/requirements.txt" || echo "Could not install Flask for VanoLeaf. Check your internet connection and run install.sh again."
echo "Setting up VanoNote ..."
"$PY" -m venv "$DIR/VanoNote/.venv"
"$DIR/VanoNote/.venv/bin/python3" -m pip install -q --upgrade pip || true
"$DIR/VanoNote/.venv/bin/python3" -m pip install -q PyMuPDF || echo "PyMuPDF was not installed: PDF import in VanoNote is limited."
chmod +x "$DIR/start.command"
if [ "$(uname)" = "Darwin" ] && [ -d "$HOME/Desktop" ]; then
  printf '#!/bin/bash\nexec "%s/start.command"\n' "$DIR" > "$HOME/Desktop/VanoAtlas.command"
  chmod +x "$HOME/Desktop/VanoAtlas.command"
  echo "Shortcut created: Desktop/VanoAtlas.command"
fi
command -v pdflatex >/dev/null 2>&1 || echo "Tip: install MacTeX or TeX Live to compile LaTeX in VanoLeaf."
echo "VanoAtlas is installed in $DIR. Starting ..."
exec "$DIR/start.command"

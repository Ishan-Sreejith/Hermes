#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m pip install --upgrade build pyinstaller

rm -rf build dist

python3 -m build

# Terminal client binary
pyinstaller --noconfirm --clean --onefile --name hermes p2pchat/main.py

# Relay server binary
pyinstaller --noconfirm --clean --onefile --name hermes-server p2pchat/hermes_server.py

# Web UI binary with bundled templates/static
pyinstaller \
  --noconfirm --clean --onefile --name web-ui \
  --add-data "p2pchat/web_ui/templates:p2pchat/web_ui/templates" \
  --add-data "p2pchat/web_ui/static:p2pchat/web_ui/static" \
  p2pchat/web_ui/app.py

echo "Build complete. Artifacts are in dist/"

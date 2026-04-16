#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Hermes Build Script v0.2.1"
echo "==========================="
echo ""

# Install build dependencies
echo "Installing build dependencies..."
python3 -m pip install --upgrade build pyinstaller

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf build dist

# Build Python package
echo "Building Python package..."
python3 -m build

# Build binaries with PyInstaller
echo ""
echo "Building binaries..."

# Terminal client binary
echo "  - Building hermes client..."
pyinstaller --noconfirm --clean --onefile --name hermes p2pchat/main.py

# Relay server binary
echo "  - Building hermes-server..."
pyinstaller --noconfirm --clean --onefile --name hermes-server p2pchat/hermes_server.py

# Web UI binary with bundled templates/static
echo "  - Building web-ui..."
pyinstaller \
  --noconfirm --clean --onefile --name web-ui \
  --add-data "p2pchat/web_ui/templates:p2pchat/web_ui/templates" \
  --add-data "p2pchat/web_ui/static:p2pchat/web_ui/static" \
  p2pchat/web_ui/server.py

echo ""
echo "==========================="
echo "Build complete!"
echo ""
echo "Artifacts:"
ls -la dist/*.exe 2>/dev/null || true
ls -la dist/*.whl 2>/dev/null || true
ls -la dist/*.tar.gz 2>/dev/null || true

echo ""
echo "To create a distributable bundle, run:"
echo "  python3 scripts/create_bundle.py"
echo ""
echo "To install locally, run:"
echo "  pip install -e ."
echo "  pip install websockets>=10.0"

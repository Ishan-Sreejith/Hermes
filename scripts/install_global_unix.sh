#!/usr/bin/env bash
set -euo pipefail

REPO="${HERMES_REPO:-Ishan-Sreejith/Hermes}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"

echo "Hermes Global-Style Installer (no project venv)"
echo "Repo: ${REPO}"
echo ""

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: ${PYTHON_BIN} not found. Install Python 3.10+ first."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl not found."
  exit 1
fi

echo "Fetching latest release metadata..."
WHEEL_URL="$(curl -fsSL "$API_URL" | "$PYTHON_BIN" -c '
import json,sys
data=json.load(sys.stdin)
assets=data.get("assets", [])
wheel=[a.get("browser_download_url","") for a in assets if a.get("name","").endswith(".whl")]
print(wheel[0] if wheel else "")
')"

if [[ -z "$WHEEL_URL" ]]; then
  echo "ERROR: No wheel asset found in latest release."
  exit 1
fi

echo "Installing/upgrading Hermes into user site-packages..."
"$PYTHON_BIN" -m pip install --user --upgrade "$WHEEL_URL"

USER_BASE="$("$PYTHON_BIN" -m site --user-base)"
BIN_DIR="${USER_BASE}/bin"

if [[ ! -x "${BIN_DIR}/hermes" ]]; then
  echo "ERROR: hermes executable not found in ${BIN_DIR} after install."
  exit 1
fi

echo "Linking commands into /usr/local/bin (requires sudo)..."
sudo ln -sf "${BIN_DIR}/hermes" /usr/local/bin/hermes
sudo ln -sf "${BIN_DIR}/hermes-server" /usr/local/bin/hermes-server
sudo ln -sf "${BIN_DIR}/web-ui" /usr/local/bin/web-ui

echo ""
echo "Install complete."
echo "Commands available globally:"
echo "  hermes"
echo "  hermes-server"
echo "  web-ui"

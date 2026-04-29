#!/usr/bin/env bash
set -euo pipefail

REPO="${HERMES_REPO:-Ishan-Sreejith/Hermes}"
INSTALL_DIR="${HERMES_INSTALL_DIR:-$HOME/.hermes}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"

echo "Hermes Release Installer"
echo "Repo: ${REPO}"
echo "Install dir: ${INSTALL_DIR}"
echo ""

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "ERROR: ${PYTHON_BIN} not found. Install Python 3.10+ first."
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl not found."
  exit 1
fi

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "Fetching latest release metadata..."
ASSET_URL="$(curl -fsSL "$API_URL" | "$PYTHON_BIN" -c '
import json,sys
data=json.load(sys.stdin)
assets=data.get("assets", [])
wheel=[a.get("browser_download_url","") for a in assets if a.get("name","").endswith(".whl")]
print(wheel[0] if wheel else "")
')"

if [[ -z "$ASSET_URL" ]]; then
  echo "ERROR: No wheel asset found in latest release."
  exit 1
fi

echo "Creating/updating virtual environment..."
"$PYTHON_BIN" -m venv venv

echo "Installing Hermes from release wheel..."
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install --upgrade "$ASSET_URL"

cat > hermes <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/hermes" "$@"
EOF

cat > hermes-server <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/hermes-server" "$@"
EOF

cat > web-ui <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/web-ui" "$@"
EOF

chmod +x hermes hermes-server web-ui

echo ""
echo "Install complete."
echo "Run:"
echo "  ${INSTALL_DIR}/hermes"
echo "  ${INSTALL_DIR}/hermes-server --port 7777"
echo "  ${INSTALL_DIR}/web-ui --port 8080"

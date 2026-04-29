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
"$PYTHON_BIN" -m pip install --user --break-system-packages --upgrade "$WHEEL_URL"

USER_BASE="$("$PYTHON_BIN" -m site --user-base)"
BIN_DIR="${USER_BASE}/bin"

if [[ ! -x "${BIN_DIR}/hermes" ]]; then
  echo "ERROR: hermes executable not found in ${BIN_DIR} after install."
  exit 1
fi

make_launcher() {
  local target="$1"
  local cmd="$2"
  cat > "$target" <<EOF
#!/usr/bin/env bash
exec "${BIN_DIR}/${cmd}" "\$@"
EOF
  chmod +x "$target"
}

try_install() {
  local dest="$1"
  local cmd="$2"
  mkdir -p "$(dirname "$dest")" 2>/dev/null || true
  make_launcher "$dest" "$cmd" 2>/dev/null
}

INSTALLED_DIR=""
if try_install "/usr/local/bin/hermes" "hermes" \
  && try_install "/usr/local/bin/hermes-server" "hermes-server" \
  && try_install "/usr/local/bin/web-ui" "web-ui"; then
  INSTALLED_DIR="/usr/local/bin"
elif try_install "$HOME/.local/bin/hermes" "hermes" \
  && try_install "$HOME/.local/bin/hermes-server" "hermes-server" \
  && try_install "$HOME/.local/bin/web-ui" "web-ui"; then
  INSTALLED_DIR="$HOME/.local/bin"
else
  echo "Need sudo to write /usr/local/bin launchers..."
  TMPDIR="$(mktemp -d)"
  make_launcher "$TMPDIR/hermes" "hermes"
  make_launcher "$TMPDIR/hermes-server" "hermes-server"
  make_launcher "$TMPDIR/web-ui" "web-ui"
  sudo cp "$TMPDIR/hermes" /usr/local/bin/hermes
  sudo cp "$TMPDIR/hermes-server" /usr/local/bin/hermes-server
  sudo cp "$TMPDIR/web-ui" /usr/local/bin/web-ui
  rm -rf "$TMPDIR"
  INSTALLED_DIR="/usr/local/bin"
fi

SHELL_RC=""
case "${SHELL:-}" in
  */zsh) SHELL_RC="$HOME/.zshrc" ;;
  */bash) SHELL_RC="$HOME/.bashrc" ;;
esac

if [[ -n "$SHELL_RC" ]] && ! echo "$PATH" | grep -q "$INSTALLED_DIR"; then
  if ! grep -q "# hermes PATH" "$SHELL_RC" 2>/dev/null; then
    echo "" >> "$SHELL_RC"
    echo "# hermes PATH" >> "$SHELL_RC"
    echo "export PATH=\"$INSTALLED_DIR:\$PATH\"" >> "$SHELL_RC"
  fi
fi

echo ""
echo "Install complete."
echo "Commands available globally:"
echo "  hermes"
echo "  hermes-server"
echo "  web-ui"

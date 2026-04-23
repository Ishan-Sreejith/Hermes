#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATH_LINE="export PATH=\"$ROOT_DIR:\$PATH\""
LOCAL_BIN="$HOME/.local/bin"
LAUNCHER="$ROOT_DIR/hermes"

add_line_if_missing() {
  local rc_file="$1"
  if [ ! -f "$rc_file" ]; then
    touch "$rc_file"
  fi

  if grep -Fqx "$PATH_LINE" "$rc_file"; then
    echo "Already present in $rc_file"
  else
    printf "\n# Hermes launcher\n%s\n" "$PATH_LINE" >> "$rc_file"
    echo "Added Hermes PATH entry to $rc_file"
  fi
}

add_line_if_missing "$HOME/.zshrc"
add_line_if_missing "$HOME/.bashrc"

mkdir -p "$LOCAL_BIN"

if [ ! -f "$LAUNCHER" ]; then
  echo "ERROR: launcher not found at $LAUNCHER"
  exit 1
fi

chmod +x "$LAUNCHER"
ln -sfn "$LAUNCHER" "$LOCAL_BIN/hermes"
ln -sfn "$LAUNCHER" "$LOCAL_BIN/hermes-local"

echo "Updated launcher links:"
echo "  $LOCAL_BIN/hermes -> $LAUNCHER"
echo "  $LOCAL_BIN/hermes-local -> $LAUNCHER"

if [ -e "/opt/homebrew/bin/hermes" ]; then
  if [ ! -L "/opt/homebrew/bin/hermes" ] || [ "$(readlink "/opt/homebrew/bin/hermes" 2>/dev/null || true)" != "$LAUNCHER" ]; then
    echo "Note: /opt/homebrew/bin/hermes exists and may shadow your launcher in some shells."
    echo "It is safe to remove with: rm -f /opt/homebrew/bin/hermes"
  fi
fi

echo ""
echo "Done. Open a new terminal or run:"
echo "  source ~/.zshrc"
echo "  rehash"
echo "Then verify with:"
echo "  which hermes"
echo "  hermes --help"

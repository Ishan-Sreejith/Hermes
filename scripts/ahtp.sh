#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LINE="export PATH=\"$ROOT_DIR:\$PATH\""

add_line_if_missing() {
  local rc_file="$1"
  if [ ! -f "$rc_file" ]; then
    touch "$rc_file"
  fi

  if grep -Fqx "$LINE" "$rc_file"; then
    echo "Already present in $rc_file"
  else
    printf "\n# Hermes launcher\n%s\n" "$LINE" >> "$rc_file"
    echo "Added Hermes PATH entry to $rc_file"
  fi
}

add_line_if_missing "$HOME/.zshrc"
add_line_if_missing "$HOME/.bashrc"

echo ""
echo "Done. Open a new terminal or run:"
echo "  source ~/.zshrc"
echo "Then verify with:"
echo "  which hermes"

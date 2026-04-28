#!/usr/bin/env bash
set -e

HERMES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERMES_DIR/venv"

DEPLOY_FIREBASE=0
DEPLOY_SCOPE="hosting"

for arg in "$@"; do
    case "$arg" in
        --deploy-firebase)
            DEPLOY_FIREBASE=1
            ;;
        --deploy-all)
            DEPLOY_FIREBASE=1
            DEPLOY_SCOPE="all"
            ;;
    esac
done

echo "🪽  Hermes Installer v0.3.0"
echo "   Project  : $HERMES_DIR"
echo "   venv     : $VENV"
echo ""

if [ ! -f "$VENV/bin/python3" ]; then
    echo "→ Creating Python virtual environment..."
    /opt/homebrew/bin/python3 -m venv "$VENV" 2>/dev/null || python3 -m venv "$VENV"
fi

echo "→ Installing p2pchat package..."
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -e "$HERMES_DIR"
echo "   ✓ Package installed ($("$VENV/bin/python3" -c 'import p2pchat; print(p2pchat.__file__)'))"

LAUNCHER_CONTENT="#!/usr/bin/env bash
exec \"$VENV/bin/hermes\" \"\$@\"
"

try_install() {
    local dest="$1"
    mkdir -p "$(dirname "$dest")" 2>/dev/null || true
    printf '%s' "$LAUNCHER_CONTENT" > "$dest" 2>/dev/null && chmod +x "$dest" 2>/dev/null
}

INSTALLED_AT=""
if try_install "/usr/local/bin/hermes"; then
    INSTALLED_AT="/usr/local/bin/hermes"
elif try_install "$HOME/.local/bin/hermes"; then
    INSTALLED_AT="$HOME/.local/bin/hermes"
fi

if [ -z "$INSTALLED_AT" ]; then
    echo "→ Need sudo to write to /usr/local/bin..."
    TMPFILE="$(mktemp)"
    printf '%s' "$LAUNCHER_CONTENT" > "$TMPFILE"
    sudo cp "$TMPFILE" /usr/local/bin/hermes
    sudo chmod +x /usr/local/bin/hermes
    rm -f "$TMPFILE"
    INSTALLED_AT="/usr/local/bin/hermes"
fi

echo "   ✓ Launcher → $INSTALLED_AT"

SHELL_RC=""
case "$SHELL" in
    */zsh)  SHELL_RC="$HOME/.zshrc" ;;
    */bash) SHELL_RC="$HOME/.bashrc" ;;
esac

NEED_PATH=0
if [ -n "$SHELL_RC" ]; then
    LAUNCHER_DIR="$(dirname "$INSTALLED_AT")"
    if ! echo "$PATH" | grep -q "$LAUNCHER_DIR"; then
        NEED_PATH=1
    fi
    if [ "$NEED_PATH" = "1" ] && ! grep -q "# hermes PATH" "$SHELL_RC" 2>/dev/null; then
        echo "" >> "$SHELL_RC"
        echo "# hermes PATH" >> "$SHELL_RC"
        echo "export PATH=\"$LAUNCHER_DIR:\$PATH\"" >> "$SHELL_RC"
        echo "   ✓ Added $LAUNCHER_DIR to PATH in $SHELL_RC"
        echo "   ⚠  Open a new terminal (or run: source $SHELL_RC) to pick up the change."
    fi
fi

echo ""
echo "✅  Done!  hermes v0.3.0 is ready."
echo "   In any new terminal, just type:  hermes"

deploy_firebase() {
    echo ""
    echo "→ Deploying to Firebase..."
    if ! command -v npx >/dev/null 2>&1; then
        echo "   ✗ npx is required for Firebase deploy. Install Node.js first."
        return 1
    fi

    if [ "$DEPLOY_SCOPE" = "hosting" ]; then
        npx -y firebase-tools@latest deploy --only hosting
    else
        npx -y firebase-tools@latest deploy
    fi
    echo "   ✓ Firebase deploy complete"
}

if [ "$DEPLOY_FIREBASE" = "1" ]; then
    deploy_firebase
else
    echo ""
    echo "ℹ️  To deploy Firebase from this script, run:"
    echo "   ./ih.sh --deploy-firebase"
    echo "   ./ih.sh --deploy-all      # deploy all configured Firebase services"
fi

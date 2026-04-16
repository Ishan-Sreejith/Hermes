#!/usr/bin/env bash
set -euo pipefail

HERMES_VERSION="0.2.1"

echo "Hermes v${HERMES_VERSION} Installer"
echo "=================================="
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found. Please install Python 3.10+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
REQUIRED_VERSION="3.10"
if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "ERROR: Python 3.10+ required, found $PYTHON_VERSION"
    exit 1
fi

echo "Python version: $PYTHON_VERSION ✓"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Create venv
echo ""
echo "Creating virtual environment..."
python3 -m venv hermes

# Activate and install
echo "Installing Hermes..."
source hermes/bin/pip install -e .

echo "Installing websockets..."
source hermes/bin/pip install "websockets>=10.0"

# Create start scripts
echo "Creating start scripts..."

cat > start.sh << 'SCRIPT'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/hermes/bin/activate"
exec hermes "$@"
SCRIPT

cat > start-server.sh << 'SCRIPT'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/hermes/bin/activate"
exec hermes-server --host 0.0.0.0 --port 7777 "$@"
SCRIPT

cat > start-web.sh << 'SCRIPT'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/hermes/bin/activate"
exec web-ui --host 0.0.0.0 --port 8080 --ws-port 8081 "$@"
SCRIPT

chmod +x start.sh start-server.sh start-web.sh

# Create README
cat > README.txt << 'README'
HERMES v0.2.1 - INSTALLED
=========================

COMMANDS:
  ./start.sh          - Terminal client (main chat app)
  ./start-server.sh   - Relay server (optional, for network connectivity)
  ./start-web.sh      - Web UI (optional, browser interface)

FIRST TIME SETUP:
  1. Create a Firebase project at console.firebase.google.com
  2. Enable Realtime Database
  3. Edit ~/.p2pchat/config.json with your Firebase config:
     {
       "cloud": {
         "backend": "firebase",
         "enabled": true,
         "project_id": "your-project-id",
         "database_url": "https://your-project.firebaseio.com"
       }
     }

FEATURES:
  - Direct peer-to-peer messaging (TCP/UDP)
  - Firebase fallback transport
  - RSA and Fernet encryption
  - Real-time WebSocket support
  - Channel-based messaging
  - File sharing
  - Message reactions
  - Typing indicators

For more info, see README.md
README

echo ""
echo "=================================="
echo "INSTALLATION COMPLETE!"
echo ""
echo "Run ./start.sh to launch Hermes"
echo ""

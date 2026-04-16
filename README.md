# Hermes v0.2.1

A terminal-first P2P chat application with local relay server, direct peer messaging, Firebase fallback, and WebSocket support.

## Quick Start

Download the bundle from releases, extract it, and run:

```
./start.sh
```

Or install from source:

```
git clone <repo>
cd hermes
./scripts/install_unix.sh
```

## Features

### Messaging
- Terminal client with interactive menu
- Direct TCP/UDP peer messaging
- Firebase fallback for reliability
- RSA and Fernet encryption
- Channel-based messaging

### Extras
- Message reactions
- Typing indicators
- Read receipts
- File sharing
- User profiles
- Channel administration
- Markdown formatting
- WebSocket server

## Requirements

- Python 3.10+
- cryptography
- flask
- websockets

## Install

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install websockets>=10.0
```

## Run

```
hermes                      # Terminal client
hermes-server --port 7777   # Relay server
web-ui --port 8080          # Web UI
```

## Distribution

Create a distributable bundle:

```
python3 scripts/create_bundle.py --format both
```

This creates hermes-0.2.1-bundle.zip and hermes-0.2.1-bundle.tar.gz

## Firebase Setup

Edit `~/.p2pchat/config.json`:

```json
{
  "cloud": {
    "backend": "firebase",
    "enabled": true,
    "project_id": "your-project-id",
    "database_url": "https://your-project.firebaseio.com"
  }
}
```

## Terminal Commands

- `/help` - Show commands
- `/join <@chan>` - Join channel
- `/peers` - List peers
- `/ping <target>` - Ping peer
- `/status` - Connection status
- `/clear` - Clear messages
- `/quit` - Exit

## Project Structure

- `p2pchat/main.py` - Terminal entry point
- `p2pchat/transport.py` - Transport layer
- `p2pchat/engine.py` - Chat engine
- `p2pchat/crypto.py` - Encryption
- `p2pchat/features.py` - Extended features
- `p2pchat/hermes_server.py` - Relay server
- `p2pchat/websocket_server.py` - WebSocket server
- `tests/` - Tests (83 total)

## License

MIT

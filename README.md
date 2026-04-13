# Hermes

Hermes is a terminal-first chat app with a local relay server, direct peer messaging, and a Firebase-backed web terminal.

## Features

- Terminal client with a startup menu
- Direct TCP messaging and UDP hole punching
- Firebase fallback transport
- Browser terminal UI that uses Firebase only
- RSA, Fernet, and plugin-based encryption
- Local config and identity storage in `~/.p2pchat`

## Requirements

- Python 3.10+
- `cryptography`
- `flask`
- Optional: Firebase project for cloud mode

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e '.[firebase]'
```

## Run

Start relay server:

```bash
hermes-server --port 7777
```

Start web UI:

```bash
web-ui --port 8080
```

Start terminal client:

```bash
hermes
```

## Firebase setup

Set Firebase values in `~/.p2pchat/config.json`:

```json
{
  "cloud": {
    "backend": "firebase",
    "enabled": true,
    "project_id": "your-project-id",
    "database_url": "https://your-project-id-default-rtdb.firebaseio.com",
    "queue_path": "messages",
    "delivery_ttl_s": 300,
    "credentials_path": "/path/to/serviceAccountKey.json"
  }
}
```

For static hosting, deploy `public/` and provide runtime config in your own environment.

## Project layout

- `p2pchat/main.py`: terminal entrypoint
- `p2pchat/transport.py`: transport logic
- `p2pchat/engine.py`: chat engine
- `p2pchat/crypto.py`: crypto and plugins
- `p2pchat/hermes_server.py`: relay server
- `p2pchat/web_ui/`: Flask and browser terminal files
- `public/`: static hosted web terminal files
- `tests/`: automated tests

## Notes

- Web UI is Firebase-only by design.
- Terminal can use direct transports first and fallback when needed.
- Firebase credentials should not be committed to git.

## License

MIT

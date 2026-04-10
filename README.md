# Hermes

Hermes is a terminal-first chat app with a relay server, direct P2P support, a simple web UI, and optional Firebase fallback.

## What it does

- Menu-first terminal client launched with `hermes`
- Direct TCP, UDP hole punching, relay fallback, and Firebase queue fallback
- RSA, Fernet, and plugin-based encryption
- Local web UI for testing and browser-based chat
- Peer discovery on the local network
- Config, identity, and key storage under `~/.p2pchat`

## Requirements

- Python 3.10+
- `cryptography`
- `flask`
- Optional: `firebase-admin` for Firebase support

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e '.[firebase]'
```

## Run locally

Start the relay server:

```bash
hermes-server --port 7777
```

Start the web UI:

```bash
web-ui --hermes localhost:7777 --port 8080
```

Start the client:

```bash
hermes --username alice
```

The client opens a menu first. Use the menu for recent chats, new contacts, channel joins, settings, and status. Advanced slash commands are still available.

## Firebase

If you want Firebase queue fallback, set these values in `~/.p2pchat/config.json`:

```json
{
  "cloud": {
    "backend": "firebase",
    "enabled": true,
    "project_id": "your-project-id",
    "database_url": "https://your-project-id-default-rtdb.firebaseio.com",
    "queue_path": "messages",
    "delivery_ttl_s": 300,
    "credentials_path": "/path/to/serviceAccountKey.json",
    "hosting_enabled": true,
    "hosting_site": "your-web-app"
  },
  "ui": {
    "hosted_web_ui_url": "https://your-web-app.web.app"
  }
}
```

For hosting the web UI on Firebase Hosting, copy the static files from `p2pchat/web_ui/templates/index.html` and `p2pchat/web_ui/static/app.js` into your Hosting public directory, then deploy with Firebase CLI.

## Web UI

The browser UI shows:

- current transport state
- direct and UDP ports
- Firebase on/off status
- message list with encryption badges
- channel and peer shortcuts

Useful endpoints while developing:

- `GET /firebase-config`
- `GET /firebase-hosting`
- `GET /status`

## Project layout

- `p2pchat/main.py` — terminal client
- `p2pchat/transport.py` — direct, UDP, relay, and cloud transport
- `p2pchat/crypto.py` — encryption and plugins
- `p2pchat/engine.py` — chat logic
- `p2pchat/hermes_server.py` — relay server
- `p2pchat/web_ui/` — local browser UI
- `tests/` — automated tests

## Notes

- Hermes relay is the easiest way to test locally.
- Hole punching needs a real network test to confirm it across NATs.
- Firebase needs a project, a database, and service account credentials.

## License

MIT

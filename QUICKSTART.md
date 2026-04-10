# P2PChat Quick Start

This guide shows how to run P2PChat locally.

## Prerequisites

- Python 3.10+
- A virtual environment is recommended
- Optional: a Firebase project and service account if you want cloud fallback

## Install

```bash
cd /Users/ishan/Hermes
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e '.[firebase]'
```

## Run the relay server

```bash
hermes-server --port 7777
```

## Run the web UI

```bash
web-ui --hermes 127.0.0.1:7777 --port 8080
```

Open `http://localhost:8080` in your browser.

## Run the client

```bash
hermes --username alice
```

The client opens a menu first. Use the menu to reach recent chats, new contacts, channels, settings, and status. You can still use slash commands in the advanced menu.

## Firebase setup

If you want Firebase queue fallback, edit `~/.p2pchat/config.json`:

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

For Firebase Hosting, copy `p2pchat/web_ui/templates/index.html` and `p2pchat/web_ui/static/app.js` into your Hosting public folder and deploy with the Firebase CLI.

## Helpful endpoints

- `GET /firebase-config`
- `GET /firebase-hosting`
- `GET /status`

## Troubleshooting

If a port is busy, choose another one:

```bash
hermes-server --port 9999
web-ui --hermes 127.0.0.1:9999 --port 9000
```

If messages do not show up:

1. Make sure Hermes is running.
2. Make sure both clients joined the same channel.
3. Check `/status` in the client.
4. Check the browser status badge.

## Next steps

- Test relay chat locally
- Try hole punching on a real network
- Enable Firebase once you have credentials

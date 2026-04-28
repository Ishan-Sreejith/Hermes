# Hermes

Hermes is a terminal-first chat app with Firebase-backed messaging, direct peer messaging, and optional web/TUI frontends.

It is built for quick local use, but you can also host the web client with Firebase Hosting.

## What you get

- Terminal chat client (`hermes`)
- Textual TUI mode (`hermes --tui`)
- Web UI (`web-ui --port 8080`)
- Relay server (`hermes-server --port 7777`)
- Channel tools (create, rename, delete, list)
- Direct messages, channel messages, and broadcast
- Network diagnostics (`--doctor`, ping, resolve, scan, LAN)

## Install

Use your own virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Or install globally:

```bash
pip install .
```

## Run

```bash
hermes
hermes --tui
hermes --doctor
hermes-server --port 7777
web-ui --port 8080
```

## Production Notes

- `web-ui` now defaults to `127.0.0.1` with debug mode disabled.
- `hermes-server` now defaults to `127.0.0.1`.
- Web UI health endpoint: `GET /healthz`

If you need public exposure, bind explicitly and run behind a reverse proxy:

```bash
hermes-server --host 0.0.0.0 --port 7777
web-ui --host 0.0.0.0 --port 8080
```

Optional runtime controls:

- `HERMES_WEB_DEBUG=1` to enable Flask debug mode (development only)
- `HERMES_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR` for relay server logging

## Firebase setup

Edit `~/.p2pchat/config.json`:

```json
{
  "cloud": {
    "backend": "firebase",
    "enabled": true,
    "project_id": "your-project-id",
    "database_url": "https://your-project-id-default-rtdb.firebaseio.com"
  }
}
```

Then deploy rules and hosting:

```bash
npx -y firebase-tools@latest deploy
```

Or use the installer helper:

```bash
./ih.sh --deploy-firebase
```

## Terminal commands

Core in-app commands:

- `/help`
- `/join @channel`
- `/connect <peer_id_or_username>`
- `/channels`
- `/create @channel`
- `/rename @old @new`
- `/delete @channel`
- `/status`
- `/peers`
- `/ping <peer|ip:port>`
- `/resolve <host>`
- `/scan <host>`
- `/lan`
- `/port show|set|random`

## Project layout

- `p2pchat/main.py` CLI entry
- `p2pchat/ui.py` curses interface
- `p2pchat/tui.py` textual interface
- `p2pchat/transport.py` transport and Firebase bridge
- `p2pchat/web_ui/` Flask + static web client
- `database.rules.json` Realtime Database rules

## Notes

- If Firebase writes fail, run `hermes --doctor` first.
- If you run into channel sync issues, check `database.rules.json` deployment status.

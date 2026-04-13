# Hermes Quick Start

## 1) Install

```bash
cd /Users/ishan/Hermes
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e '.[firebase]'
```

## 2) Start relay server

```bash
hermes-server --port 7777
```

## 3) Start web terminal

```bash
web-ui --port 8080
```

Open `http://localhost:8080`.

## 4) Start terminal client

```bash
hermes
```

## 5) Use common commands

- `/help`
- `/join <@channel>`
- `/connect <peer_id>`
- `/load [n|on|off]`
- `/status`

## Firebase

If you want Firebase mode, set values in `~/.p2pchat/config.json`:

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

## Troubleshooting

- Port in use: choose another port.
- No messages: check active channel and `/status`.
- Web not loading: verify Firebase config and rules.

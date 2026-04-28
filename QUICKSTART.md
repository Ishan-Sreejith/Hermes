# Quick Start

If you want the fastest route to a working Hermes setup, use this.

## 1) Install

```bash
cd /Users/ishan/Hermes
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2) Verify environment

```bash
hermes --doctor
```

If anything is missing, fix it before continuing.

## 3) Start services

Terminal app:

```bash
hermes
```

Optional TUI:

```bash
hermes --tui
```

Optional web UI:

```bash
web-ui --port 8080
```

## 4) In-app basics

- `/help`
- `/join @team`
- `/connect <peer_or_username>`
- `/create @team`
- `/rename @team @newteam`
- `/delete @newteam`

## 5) Firebase deploy

```bash
./ih.sh --deploy-firebase
```

Or full deploy:

```bash
./ih.sh --deploy-all
```

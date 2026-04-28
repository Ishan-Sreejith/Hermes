# Hermes CLI Global Use

If you want `hermes` available from any directory, this is the easiest flow.

## Global install

```bash
pip install .
```

User-only install (no sudo):

```bash
pip install --user .
```

If needed:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Commands

```bash
hermes
hermes --tui
hermes --doctor
hermes-server --port 7777
web-ui --port 8080
```

## Common issues

- `command not found: hermes` → check your PATH and install target.
- `ModuleNotFoundError` → reinstall with `pip install -e .`.
- Firebase errors → run `hermes --doctor` and confirm database URL/rules.

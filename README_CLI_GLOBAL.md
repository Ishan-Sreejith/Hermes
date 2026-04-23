# Hermes CLI: Global Installation and Usage

## Global Install (Recommended)

If you want to use the `hermes` command from any directory, install globally:

```sh
pip install .
```

Or for user-local (no sudo required):

```sh
pip install --user .
```

Make sure `~/.local/bin` is on your `$PATH` (for user-local installs):

```sh
export PATH="$HOME/.local/bin:$PATH"
```

## Running Hermes

- Single-command launcher from repo root (auto-uses `.venv`/`venv` if present):
  ```sh
  ./hermes
  ```
- Single-command launcher for relay server and web UI:
  ```sh
  ./hermes server --port 7777
  ./hermes web --port 8080
  ```
- To start the terminal client:
  ```sh
  hermes
  ```
- To start the relay server:
  ```sh
  hermes-server --port 7777
  ```
- To start the web UI:
  ```sh
  web-ui --port 8080
  ```

## Portable Script

If you want to use the venv, you can use the provided scripts:

```sh
./hermes
./start.sh
```

`./hermes` runs without manual activation and will prefer `.venv`/`venv` binaries when available.

## Troubleshooting

- If you get `command not found: hermes`, make sure you installed with `pip install .` and your `$PATH` is correct.
- If you get `ModuleNotFoundError`, make sure you installed dependencies with `pip install .` or `pip install -e .`.
- If you see UI lag or message delay, please report the issue with details.

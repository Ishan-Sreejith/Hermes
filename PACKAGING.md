# Packaging and Distribution

This project supports two distribution modes:

1. Python package (`wheel` / `sdist`)
2. Standalone binaries (`hermes`, `hermes-server`, `web-ui`)

## Quick local install

```bash
./scripts/install_local.sh
```

Then run:

```bash
hermes
hermes-server --port 7777
web-ui --port 8080
```

## Build distributable package files

```bash
python3 -m pip install --upgrade build
python3 -m build
```

Artifacts:

- `dist/p2pchat-<version>.whl`
- `dist/p2pchat-<version>.tar.gz`

Install from wheel:

```bash
python3 -m pip install dist/p2pchat-*.whl
```

## Build standalone binaries

```bash
./scripts/build_release.sh
```

Artifacts:

- `dist/hermes`
- `dist/hermes-server`
- `dist/web-ui`

Run binaries directly:

```bash
./dist/hermes
./dist/hermes-server --port 7777
./dist/web-ui --port 8080
```

## Notes

- Web UI binaries use Firebase-backed web terminal mode.
- Keep Firebase secrets in local config files only.
- Do not commit credentials to git.

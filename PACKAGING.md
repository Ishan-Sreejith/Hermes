# Packaging

Hermes supports both Python package distribution and static web hosting deployment.

## Build Python artifacts

```bash
python3 -m pip install --upgrade build
python3 -m build
```

Output:

- `dist/p2pchat-<version>.whl`
- `dist/p2pchat-<version>.tar.gz`

Install wheel:

```bash
python3 -m pip install dist/p2pchat-*.whl
```

## Local install helper

```bash
./ih.sh
```

This installs the package in `venv/` and creates a `hermes` launcher.

## Firebase hosting package/deploy

Static assets come from:

- `p2pchat/web_ui/public`

Deploy hosting only:

```bash
./ih.sh --deploy-firebase
```

Deploy all configured Firebase resources (rules + hosting):

```bash
./ih.sh --deploy-all
```

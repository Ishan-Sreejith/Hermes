# Hermes Binary-Style Release Install

Use the release installer scripts instead of cloning source code.

## macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/Ishan-Sreejith/Hermes/main/scripts/install_release_unix.sh -o /tmp/install_hermes.sh
bash /tmp/install_hermes.sh
```

## Windows (PowerShell)

```powershell
iwr https://raw.githubusercontent.com/Ishan-Sreejith/Hermes/main/scripts/install_release_windows.ps1 -OutFile $env:TEMP\install_hermes.ps1
powershell -ExecutionPolicy Bypass -File $env:TEMP\install_hermes.ps1
```

## Behavior

- Fetches latest GitHub release metadata
- Downloads the release wheel artifact (`.whl`)
- Installs/upgrades into `~/.hermes/venv`
- Creates launchers in `~/.hermes`:
  - `hermes`
  - `hermes-server`
  - `web-ui`

This updates existing Hermes installs in place when re-run.

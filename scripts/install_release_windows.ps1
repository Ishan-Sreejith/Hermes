$ErrorActionPreference = "Stop"

$Repo = if ($env:HERMES_REPO) { $env:HERMES_REPO } else { "Ishan-Sreejith/Hermes" }
$InstallDir = if ($env:HERMES_INSTALL_DIR) { $env:HERMES_INSTALL_DIR } else { Join-Path $HOME ".hermes" }
$ApiUrl = "https://api.github.com/repos/$Repo/releases/latest"

Write-Host "Hermes Release Installer"
Write-Host "Repo: $Repo"
Write-Host "Install dir: $InstallDir"
Write-Host ""

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Python not found. Install Python 3.10+ first."
}

New-Item -Path $InstallDir -ItemType Directory -Force | Out-Null
Set-Location $InstallDir

Write-Host "Fetching latest release metadata..."
$release = Invoke-RestMethod -Uri $ApiUrl
$wheel = $release.assets | Where-Object { $_.name -like "*.whl" } | Select-Object -First 1
if (-not $wheel) {
  throw "No wheel asset found in latest release."
}

Write-Host "Creating/updating virtual environment..."
python -m venv venv

Write-Host "Installing Hermes from release wheel..."
& "$InstallDir\venv\Scripts\python.exe" -m pip install --upgrade pip
& "$InstallDir\venv\Scripts\python.exe" -m pip install --upgrade $wheel.browser_download_url

@'
@echo off
"%~dp0venv\Scripts\hermes.exe" %*
'@ | Set-Content -Path "$InstallDir\hermes.bat" -Encoding ASCII

@'
@echo off
"%~dp0venv\Scripts\hermes-server.exe" %*
'@ | Set-Content -Path "$InstallDir\hermes-server.bat" -Encoding ASCII

@'
@echo off
"%~dp0venv\Scripts\web-ui.exe" %*
'@ | Set-Content -Path "$InstallDir\web-ui.bat" -Encoding ASCII

Write-Host ""
Write-Host "Install complete."
Write-Host "Run:"
Write-Host "  $InstallDir\hermes.bat"
Write-Host "  $InstallDir\hermes-server.bat --port 7777"
Write-Host "  $InstallDir\web-ui.bat --port 8080"

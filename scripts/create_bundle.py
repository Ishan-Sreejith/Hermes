#!/usr/bin/env python3
"""
Hermes Bundle Creator
Creates a distributable bundle with all Hermes components ready to use.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


HERMES_VERSION = "0.3.0"


def run_command(cmd: list[str], cwd: Path | None = None, env: dict | None = None):
    print(f"Running: {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=cwd, env=env)
    if result.returncode != 0:
        print(f"Command failed with code {result.returncode}")
        sys.exit(1)


def create_python_bundle(root_dir: Path, output_dir: Path):
    print("\n=== Creating Python Package Bundle ===")

    bundle_dir = output_dir / f"hermes-{HERMES_VERSION}-bundle"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True)

    (bundle_dir / "hermes").mkdir()
    (bundle_dir / "hermes" / "bin").mkdir()
    (bundle_dir / "hermes" / "lib").mkdir()
    (bundle_dir / "hermes" / "lib" / "python3.10").mkdir()
    (bundle_dir / "hermes" / "include").mkdir()

    print("Creating virtual environment...")
    venv_dir = bundle_dir / "hermes"

    run_command([sys.executable, "-m", "venv", str(venv_dir)])

    pip = venv_dir / "bin" / "pip"

    print("Installing Hermes package...")
    run_command([str(pip), "install", "-e", str(root_dir)])

    print("Installing websockets for WebSocket support...")
    run_command([str(pip), "install", "websockets>=10.0"])

    print("Copying startup scripts...")
    with open(bundle_dir / "start.sh", "w") as f:
        f.write(f"""#!/bin/bash

HERMES_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$HERMES_DIR/hermes/bin/activate"

if [ ! -f ~/.p2pchat/config.json ]; then
    echo "First time setup detected!"
    echo "Creating config at ~/.p2pchat/"
    mkdir -p ~/.p2pchat
fi

echo "Starting Hermes v{HERMES_VERSION}..."
echo "Commands:"
echo "  hermes              - Start terminal client"
echo "  hermes-server      - Start relay server"
echo "  web-ui              - Start web UI (Flask + WebSocket)"
echo ""

exec "$HERMES_DIR/hermes/bin/hermes" "$@"
""")

    with open(bundle_dir / "start-server.sh", "w") as f:
        f.write(f"""#!/bin/bash

HERMES_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$HERMES_DIR/hermes/bin/activate"

echo "Starting Hermes Relay Server v{HERMES_VERSION}..."
exec "$HERMES_DIR/hermes/bin/hermes-server" --host 0.0.0.0 --port 7777 "$@"
""")

    with open(bundle_dir / "start-web.sh", "w") as f:
        f.write(f"""#!/bin/bash

HERMES_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$HERMES_DIR/hermes/bin/activate"

echo "Starting Hermes Web UI v{HERMES_VERSION}..."
echo "Web UI: http://localhost:8080"
echo "WebSocket: ws://localhost:8081"
exec "$HERMES_DIR/hermes/bin/web-ui" --host 0.0.0.0 --port 8080 --ws-port 8081 "$@"
""")

    for script in ["start.sh", "start-server.sh", "start-web.sh"]:
        (bundle_dir / script).chmod(0o755)

    print("Creating README...")
    with open(bundle_dir / "README.txt", "w") as f:
        f.write(f"""HERMES v{HERMES_VERSION}
=============
A terminal-first P2P chat app with relay server, Firebase fallback, and Web UI.

QUICK START
----------
1. Run ./start.sh to start the terminal client
2. Run ./start-server.sh to start the relay server (optional)
3. Run ./start-web.sh to start the web UI (optional)

FEATURES
--------
- Direct peer-to-peer messaging (TCP/UDP)
- Firebase fallback transport
- RSA and Fernet encryption
- Real-time WebSocket support
- Channel-based messaging
- File sharing

TERMINAL COMMANDS
-----------------
/help      - Show all commands
/join      - Join a channel
/connect   - Connect to a peer
/peers     - List online peers
/ping      - Ping a peer
/status    - Show connection status
/clear     - Clear messages
/quit      - Exit

FIRST TIME SETUP
-----------------
1. Create a Firebase project at console.firebase.google.com
2. Enable Realtime Database
3. Set database rules (see Firebase setup in README.md)
4. Edit ~/.p2pchat/config.json with your Firebase config

FIREBASE CONFIG EXAMPLE
----------------------
{{
  "cloud": {{
    "backend": "firebase",
    "enabled": true,
    "project_id": "your-project-id",
    "database_url": "https://your-project.firebaseio.com"
  }}
}}

LICENSE: MIT
""")

    return bundle_dir


def create_archive(bundle_dir: Path, output_dir: Path, format: str = "zip"):
    print(f"\n=== Creating {format.upper()} Archive ===")

    if format == "zip":
        archive_path = output_dir / f"hermes-{HERMES_VERSION}-{format}.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in bundle_dir.rglob("*"):
                if file_path.is_file():
                    arcname = file_path.relative_to(bundle_dir.parent)
                    zf.write(file_path, arcname)
    else:
        archive_path = output_dir / f"hermes-{HERMES_VERSION}-{format}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tf:
            tf.add(bundle_dir, arcname=f"hermes-{HERMES_VERSION}-bundle")

    print(f"Created: {archive_path}")
    print(f"Size: {archive_path.stat().st_size / 1024 / 1024:.2f} MB")
    return archive_path


def main():
    parser = argparse.ArgumentParser(description="Create Hermes distributable bundle")
    parser.add_argument(
        "--format",
        choices=["zip", "tar.gz", "both"],
        default="both",
        help="Archive format to create",
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=Path("dist"), help="Output directory"
    )
    args = parser.parse_args()

    root_dir = Path(__file__).parent.parent
    output_dir = args.output
    output_dir.mkdir(exist_ok=True)

    print(f"Creating Hermes v{HERMES_VERSION} bundle...")

    bundle_dir = create_python_bundle(root_dir, output_dir)

    if args.format == "zip":
        create_archive(bundle_dir, output_dir, "zip")
    elif args.format == "tar.gz":
        create_archive(bundle_dir, output_dir, "tar.gz")
    else:
        create_archive(bundle_dir, output_dir, "zip")
        create_archive(bundle_dir, output_dir, "tar.gz")

    print("\n=== Bundle Complete ===")
    print(f"Files created in: {output_dir}")
    print("\nTo use:")
    print(f"  1. Extract the archive")
    print(f"  2. Run ./start.sh to launch Hermes")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import asyncio
import threading
import time
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from ..config import ConfigManager
from ..protocol import read_message, write_message

message_queue = deque(maxlen=500)
app = Flask(__name__, template_folder="templates", static_folder="static")

hermes_reader: asyncio.StreamReader | None = None
hermes_writer: asyncio.StreamWriter | None = None
hermes_host = "127.0.0.1"
hermes_port = 7777
known_peers: dict[str, dict[str, str]] = {}
loop: asyncio.AbstractEventLoop | None = None
client_peer_id = "web-ui-observer"
config_dir = Path.home() / ".p2pchat"
config_mgr = ConfigManager(config_dir)
transport_status = {
    "connected": True,
    "transport_mode": "firebase-web",
    "last_transport": "firebase",
    "last_error": None,
    "direct_port": None,
    "udp_port": None,
    "firebase_enabled": False,
}


def _current_config() -> dict:
    cfg = config_mgr.load()
    transport_status["transport_mode"] = "firebase-web"
    transport_status["firebase_enabled"] = cfg.cloud.enabled
    return {
        "transport_mode": cfg.transport_mode,
        "direct_timeout_s": cfg.direct_timeout_s,
        "holepunch_timeout_s": cfg.holepunch_timeout_s,
        "hermes_host": cfg.hermes_host,
        "hermes_port": cfg.hermes_port,
        "stun_host": cfg.stun_host,
        "stun_port": cfg.stun_port,
        "cloud": {
            "enabled": cfg.cloud.enabled,
            "backend": cfg.cloud.backend,
            "project_id": cfg.cloud.project_id,
            "database_url": cfg.cloud.database_url,
            "queue_path": cfg.cloud.queue_path,
            "hosting_enabled": cfg.cloud.hosting_enabled,
            "hosting_site": cfg.cloud.hosting_site,
        },
        "ui": {
            "hosted_web_ui_url": cfg.ui.hosted_web_ui_url,
            "compact_tui": cfg.ui.compact_tui,
        },
        "firebase_web": {
            "apiKey": cfg.cloud.api_key,
            "databaseURL": cfg.cloud.database_url,
            "projectId": cfg.cloud.project_id,
            "authDomain": cfg.cloud.project_id and f"{cfg.cloud.project_id}.firebaseapp.com",
            "appId": cfg.cloud.app_id,
        },
        "transport": transport_status,
    }


async def hermes_receive_loop():
    global hermes_reader, hermes_writer
    try:
        while hermes_reader and hermes_writer:
            msg = await read_message(hermes_reader)
            if msg is None:
                break

            from_id = str(msg.get("from_id") or "")
            if from_id:
                known_peers[from_id] = {
                    "peer_id": from_id,
                    "name": str(msg.get("from_name") or from_id),
                }

            message_queue.append(
                {
                    "ts": float(msg.get("ts") or time.time()),
                    "from_name": msg.get("from_name", "unknown"),
                    "from_id": from_id,
                    "to": msg.get("to", ""),
                    "body": msg.get("body", ""),
                    "enc": msg.get("enc", "none"),
                    "channel": msg.get("channel"),
                }
            )
    finally:
        hermes_reader = None
        hermes_writer = None
        transport_status["connected"] = False


async def connect_hermes():
    global hermes_reader, hermes_writer
    try:
        hermes_reader, hermes_writer = await asyncio.open_connection(hermes_host, hermes_port)
        await write_message(
            hermes_writer,
            {
                "type": "register",
                "peer_id": client_peer_id,
                # Aligning with @ prefix for groups/channels
                "channels": ["@broadcast", "@dev"],
            },
        )
        transport_status["connected"] = True
        asyncio.create_task(hermes_receive_loop())
        print(f"Connected to Hermes at {hermes_host}:{hermes_port}")
    except Exception as e:
        hermes_reader = None
        hermes_writer = None
        transport_status["connected"] = False
        transport_status["last_error"] = str(e)
        print(f"Could not connect to Hermes: {e}")


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/firebase-config")
def firebase_config():
    return jsonify(_current_config())


@app.get("/messages")
def messages():
    try:
        since = float(request.args.get("since", 0))
    except (TypeError, ValueError):
        since = 0.0
    return jsonify([m for m in list(message_queue) if float(m.get("ts", 0) or 0) > since])


@app.post("/send")
def send():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid payload"}), 400

    body = str(payload.get("body", "")).strip()
    if not body:
        return jsonify({"ok": False, "error": "empty message"}), 400

    to_target = str(payload.get("to") or "*")
    # Ensure targets starting with @ are handled correctly
    channel = payload.get("channel")
    if to_target.startswith("@"):
        channel = to_target

    msg = {
        "type": "msg",
        "body": body,
        "from_id": client_peer_id,
        "from_name": "web-ui",
        "to": to_target,
        "channel": channel,
        "ts": float(payload.get("ts") or time.time()),
        "enc": str(payload.get("enc") or "none"),
    }

    if hermes_writer and loop:
        try:
            fut = asyncio.run_coroutine_threadsafe(
                write_message(hermes_writer, {"type": "relay", "to": msg["to"], "body": msg}),
                loop,
            )
            if not fut.result(timeout=2):
                return jsonify({"ok": False, "error": "relay send failed"}), 502
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    message_queue.append(
        {
            "ts": msg["ts"],
            "from_name": msg["from_name"],
            "from_id": msg["from_id"],
            "to": msg["to"],
            "channel": msg["channel"],
            "body": msg["body"],
            "enc": msg["enc"],
        }
    )

    return jsonify({"ok": True})


@app.get("/peers")
def peers():
    return jsonify(list(known_peers.values()))


@app.get("/status")
def status():
    return jsonify(
        {
            **transport_status,
            "hermes_host": hermes_host,
            "hermes_port": hermes_port,
        }
    )


@app.get("/firebase-hosting")
def firebase_hosting():
    cfg = _current_config()
    return jsonify(
        {
            "hosting_enabled": cfg["cloud"]["hosting_enabled"],
            "hosting_site": cfg["cloud"]["hosting_site"],
            "static_assets": ["/", "/app.js", "/static/"],
            "deployment_hint": "Serve the static web UI from Firebase Hosting and keep Flask for local polling only.",
            "transport_mode": cfg["transport_mode"],
            "stun_host": cfg["stun_host"],
            "stun_port": cfg["stun_port"],
        }
    )


@app.get("/web-config")
def web_config():
    return jsonify(_current_config())


def run_asyncio_loop(loop_obj: asyncio.AbstractEventLoop):
    global loop
    loop = loop_obj
    asyncio.set_event_loop(loop_obj)
    loop_obj.run_forever()


def main():
    global hermes_host, hermes_port

    parser = argparse.ArgumentParser(description="P2PChat web UI")
    parser.add_argument("--hermes", default="127.0.0.1:7777", help="Hermes server address")
    parser.add_argument("--port", type=int, default=8080, help="Web UI port")
    parser.add_argument("--config-dir", default=str(Path.home() / ".p2pchat"), help="Config directory")
    args = parser.parse_args()

    global config_dir, config_mgr
    config_dir = Path(args.config_dir)
    config_mgr = ConfigManager(config_dir)

    if ":" in args.hermes:
        hermes_host, hermes_port_str = args.hermes.rsplit(":", 1)
        hermes_port = int(hermes_port_str)
    else:
        hermes_host = args.hermes

    event_loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=run_asyncio_loop, args=(event_loop,), daemon=True)
    loop_thread.start()

    # Web terminal mode is Firebase-only; no Hermes socket bootstrap.

    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

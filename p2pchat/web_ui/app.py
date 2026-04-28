from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import threading
from collections import deque
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_from_directory,
)

message_queue = deque(maxlen=500)
app = Flask(__name__, template_folder="templates", static_folder="static")

config_dir = Path.home() / ".p2pchat"


def _get_fb_config():
    return {
        "apiKey": os.getenv("FIREBASE_API_KEY", "YOUR_API_KEY"),
        "databaseURL": os.getenv(
            "FIREBASE_DATABASE_URL", "https://YOUR_PROJECT.firebaseio.com"
        ),
        "projectId": os.getenv("FIREBASE_PROJECT_ID", "YOUR_PROJECT_ID"),
        "authDomain": os.getenv("FIREBASE_PROJECT_ID", "YOUR_PROJECT_ID")
        + ".firebaseapp.com",
        "appId": os.getenv("FIREBASE_APP_ID", "YOUR_APP_ID"),
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/app.js")
def app_js():
    return send_from_directory(app.static_folder, "app.js")


@app.get("/firebase-config.js")
def firebase_config_js():
    conf = _get_fb_config()
    body = f"window.HERMES_FIREBASE_CONFIG = {{'firebase_web': {json.dumps(conf)}}};"
    return Response(body, mimetype="application/javascript")


@app.get("/web-config")
def web_config():
    return jsonify({"firebase_web": _get_fb_config()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port, debug=True)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import os
from collections import deque

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


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _has_placeholder_firebase_config(conf: dict) -> bool:
    return any(
        str(value).startswith("YOUR_")
        for value in [conf.get("apiKey"), conf.get("projectId"), conf.get("appId")]
    )


@app.after_request
def add_security_headers(response: Response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


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
    response = Response(body, mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/web-config")
def web_config():
    return jsonify({"firebase_web": _get_fb_config()})


@app.get("/healthz")
def healthz():
    conf = _get_fb_config()
    return jsonify(
        {
            "ok": True,
            "firebase_config_present": not _has_placeholder_firebase_config(conf),
        }
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("HERMES_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("HERMES_WEB_PORT", "8080")))
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode (not for production)",
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    args = parser.parse_args()
    if args.version:
        print("0.3.0")
        return
    debug = args.debug or _bool_env("HERMES_WEB_DEBUG", False)
    app.run(host=args.host, port=args.port, debug=debug, use_reloader=debug, threaded=True)


if __name__ == "__main__":
    main()

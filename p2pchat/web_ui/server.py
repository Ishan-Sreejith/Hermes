import asyncio
import argparse
from flask import Flask, render_template, request, jsonify
from p2pchat.websocket_server import WebSocketServer, WEBSOCKETS_AVAILABLE
import threading

app = Flask(__name__)

ws_server = None
ws_thread = None


class HermesWebUI:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080, ws_port: int = 8081):
        self.host = host
        self.port = port
        self.ws_port = ws_port
        self.app = app
        self._setup_routes()
        self._setup_websocket()

    def _setup_websocket(self):
        global ws_server, ws_thread
        ws_server = WebSocketServer(port=self.ws_port)

        def run_ws():
            asyncio.run(ws_server.start())

        ws_thread = threading.Thread(target=run_ws, daemon=True)
        ws_thread.start()

    def _setup_routes(self):
        @self.app.route("/")
        def index():
            return render_template("index.html")

        @self.app.route("/api/status")
        def status():
            return jsonify(
                {
                    "status": "ok",
                    "websocket_port": self.ws_port,
                    "websocket_available": WEBSOCKETS_AVAILABLE,
                    "clients": len(ws_server._clients) if ws_server else 0,
                }
            )

        @self.app.route("/api/channels")
        def channels():
            if not ws_server:
                return jsonify([])
            channels = []
            for name, client_ids in ws_server._channels.items():
                channels.append({"name": name, "clients": len(client_ids)})
            return jsonify(channels)

        @self.app.route("/api/broadcast", methods=["POST"])
        def broadcast():
            data = request.json
            message = data.get("message", "")
            channel = data.get("channel", "broadcast")

            if ws_server:
                asyncio.run(
                    ws_server._broadcast_to_channel(
                        channel,
                        {
                            "type": "broadcast",
                            "body": message,
                            "ts": asyncio.get_event_loop().time(),
                        },
                    )
                )

            return jsonify({"ok": True})


def main():
    parser = argparse.ArgumentParser(description="Hermes Web UI")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    parser.add_argument("--ws-port", type=int, default=8081, help="WebSocket port")
    args = parser.parse_args()

    ui = HermesWebUI(host=args.host, port=args.port, ws_port=args.ws_port)
    print(f"Starting Hermes Web UI on http://{args.host}:{args.port}")
    print(f"WebSocket server on ws://{args.host}:{args.ws_port}")
    ui.app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

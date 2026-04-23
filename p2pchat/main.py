from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
import threading
from pathlib import Path

from .config import ConfigManager
from .crypto import CryptoManager
from .engine import Engine
from .identity import load_or_create
from .transport import TransportManager
from .ui import ChatUI

logging.getLogger("firebase_admin").setLevel(logging.WARNING)


class CLIApp:
    def __init__(
        self,
        username: str | None = None,
        home: Path | None = None,
        listen_port: int | None = None,
    ):
        self.home = home or (Path.home() / ".p2pchat")
        self.identity = load_or_create(self.home, username=username)
        self.listen_port = listen_port
        self.config_mgr = ConfigManager(self.home)
        self.config = self.config_mgr.load()

        self.transport = TransportManager(self.config, self.identity)
        self.crypto = CryptoManager(self.identity)
        self.engine = Engine(self.identity, self.config, self.transport, self.crypto)
        self.ui = ChatUI(self.identity, self.engine, self.config)

        self.engine.set_ui(self.ui)
        self.transport.set_on_message(self.engine.on_message)

    async def startup_menu(self) -> str:
        while True:
            print("\n--- Start Menu ---")
            print("1) Open @broadcast")
            print("2) Join a channel")
            print("3) Set custom listen port")
            print("4) Network tools")
            print("5) Quit")
            choice = input("Choose [1]: ").strip() or "1"

            if choice == "1":
                return "@broadcast"
            if choice == "2":
                channel = (
                    input("Channel name (example: @dev): ").strip() or "@broadcast"
                )
                if not channel.startswith("@"):
                    channel = "@" + channel
                return channel
            if choice == "3":
                raw = input("Listen port (1-65535): ").strip()
                try:
                    port = int(raw)
                    if port < 1 or port > 65535:
                        raise ValueError
                except ValueError:
                    print("Invalid port.")
                    continue
                bound = await self.transport.set_listen_port(port)
                self.listen_port = bound
                print(f"Now listening on {bound}.")
                continue
            if choice == "4":
                await self.network_tools_menu()
                continue
            if choice == "5":
                sys.exit(0)
            print("Invalid menu option.")

    async def network_tools_menu(self):
        while True:
            print("\n--- Network Tools ---")
            print("1) Show network status")
            print("2) List online peers")
            print("3) Ping peer by id")
            print("4) Ping host:port")
            print("5) Resolve hostname (DNS)")
            print("6) Scan common ports on host")
            print("7) Show LAN devices (ARP)")
            print("8) Set listen port")
            print("9) Create random listen port")
            print("10) Back")
            choice = input("Choose [10]: ").strip() or "10"

            if choice == "1":
                s = self.transport.status
                print(f"Public IP: {s.get('ip')}")
                print(f"TCP listen: {s.get('port')}")
                print(f"UDP listen: {s.get('udp_port')}")
                print(f"Last transport: {s.get('last_transport')}")
                continue
            if choice == "2":
                peers = self.transport.list_online_peers()
                if not peers:
                    print("No peers online.")
                else:
                    for p in peers:
                        print(
                            f"- {p['name']} ({p['id']}) {p.get('ip')}:{p.get('port')}"
                        )
                continue
            if choice == "3":
                peer_id = input("Peer id: ").strip()
                if not peer_id:
                    print("Peer id is required.")
                    continue
                result = await self.transport.ping_peer(peer_id)
                if result.get("ok"):
                    print(f"Ping ok: {peer_id} in {result.get('latency_ms')} ms")
                else:
                    print(f"Ping failed: {result.get('error', 'unknown error')}")
                continue
            if choice == "4":
                target = input("Host:port (default port 80): ").strip()
                if not target:
                    print("Target is required.")
                    continue
                if ":" in target:
                    host, port_s = target.rsplit(":", 1)
                    try:
                        port = int(port_s)
                    except ValueError:
                        print("Invalid port.")
                        continue
                else:
                    host = target
                    port = 80
                result = await self.transport.ping_host(host, port)
                if result.get("ok"):
                    print(f"Ping ok: {host}:{port} in {result.get('latency_ms')} ms")
                else:
                    print(f"Ping failed: {result.get('error', 'unknown error')}")
                continue
            if choice == "5":
                host = input("Hostname/IP: ").strip()
                if not host:
                    print("Hostname/IP is required.")
                    continue
                result = await self.transport.resolve_host(host)
                if not result.get("ok"):
                    print(f"Resolve failed: {result.get('error', 'unknown error')}")
                    continue
                print(f"Resolved in {result.get('latency_ms')} ms:")
                for addr in result.get("addresses", []):
                    print(f"- {addr}")
                continue
            if choice == "6":
                host = input("Host/IP to scan: ").strip()
                if not host:
                    print("Host/IP is required.")
                    continue
                result = await self.transport.scan_common_ports(host)
                open_ports = result.get("open_ports") or []
                print(
                    f"Scan finished in {result.get('elapsed_ms')} ms, checked {result.get('checked')} ports."
                )
                if not open_ports:
                    print("No common open ports found.")
                else:
                    print("Open ports:")
                    for p in open_ports:
                        print(f"- {host}:{p['port']} ({p['latency_ms']} ms)")
                continue
            if choice == "7":
                result = await self.transport.list_lan_devices()
                if not result.get("ok"):
                    print(
                        f"LAN discovery failed: {result.get('error', 'unknown error')}"
                    )
                    continue
                print(f"LAN devices in ARP cache: {result.get('count')}")
                for d in result.get("devices", []):
                    print(f"- {d['ip']} {d['mac']} ({d['host']})")
                continue
            if choice == "8":
                raw = input("Listen port (1-65535): ").strip()
                try:
                    port = int(raw)
                    if port < 1 or port > 65535:
                        raise ValueError
                except ValueError:
                    print("Invalid port.")
                    continue
                bound = await self.transport.set_listen_port(port)
                self.listen_port = bound
                print(f"Now listening on {bound}.")
                continue
            if choice == "9":
                bound = await self.transport.create_random_listen_port()
                self.listen_port = bound
                print(f"Random listen port created: {bound}")
                continue
            if choice == "10":
                return
            print("Invalid menu option.")

    async def pre_connect(self):
        """Phase 1 blocking I/O before the background loop."""
        print("\n--- Hermes Messenger ---")
        u = input("Username: ").strip()
        p = getpass.getpass("Password: ").strip()

        if not await self.transport.initialize(listen_port=self.listen_port):
            print("Error: Could not initialize Firebase backend.")
            sys.exit(1)

        res = await self.transport.authenticate(u, p)
        if not res.get("ok"):
            print(f"Auth failed: {res.get('error', 'Unknown error')}")
            sys.exit(1)

        self.identity.peer_id = res["peer_id"]
        self.identity.username = res["username"]
        print(f"Connected as {u} ({self.identity.peer_id})")

        return await self.startup_menu()

    async def post_connect(self, loop, initial_channel: str):
        """Phase 2 runs inside run_forever."""
        self.engine.set_loop(loop)
        self.transport.update_presence()
        await self.transport.start_personal_inbox_listener()
        await self.engine.join_channel(initial_channel)
        self.ui.active_channel = initial_channel
        self.ui._dirty.set()  # trigger first redraw

        while True:
            try:
                self.transport.update_presence()
            except Exception:
                pass
            await asyncio.sleep(60)

    def run(self):
        setup_loop = asyncio.new_event_loop()
        initial_channel = setup_loop.run_until_complete(self.pre_connect())
        setup_loop.close()

        loop = asyncio.new_event_loop()

        def run_loop(l):
            asyncio.set_event_loop(l)
            l.run_forever()

        t = threading.Thread(target=run_loop, args=(loop,), daemon=True)
        t.start()

        import time as _time
        _time.sleep(0.05)
        asyncio.run_coroutine_threadsafe(self.post_connect(loop, initial_channel), loop)

        self.ui.run()

        loop.call_soon_threadsafe(loop.stop)


def main():
    parser = argparse.ArgumentParser(
        description="Hermes - Terminal P2P Messenger v" + __import__('p2pchat.ui', fromlist=['VERSION']).VERSION
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        default=None,
        metavar="PORT",
        help="Bind direct P2P listener to this port (default: auto)",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch optional Textual TUI (requires textual package)",
    )
    args = parser.parse_args()

    if args.tui:
        try:
            from .tui import run_textual_tui
            run_textual_tui(listen_port=args.listen_port)
            return
        except ModuleNotFoundError as exc:
            if exc.name == "textual":
                print("[hermes] Textual is not installed. Install with: pip install textual")
                print("[hermes] Falling back to CLI...")
            else:
                raise

    try:
        app = CLIApp(listen_port=args.listen_port)
        app.run()
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")


if __name__ == "__main__":
    main()

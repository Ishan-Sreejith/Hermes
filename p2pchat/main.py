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
    def __init__(self, username: str | None = None, home: Path | None = None, listen_port: int | None = None):
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
            print("4) Quit")
            choice = (input("Choose [1]: ").strip() or "1")

            if choice == "1":
                return "@broadcast"
            if choice == "2":
                channel = input("Channel name (example: @dev): ").strip() or "@broadcast"
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
                sys.exit(0)
            print("Invalid menu option.")

    async def initialize(self):
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

        self.transport.update_presence()

        initial_channel = await self.startup_menu()
        await self.engine.join_channel(initial_channel)
        self.ui.active_channel = initial_channel

    async def start_engine(self, loop):
        self.engine.set_loop(loop)
        while True:
            try:
                self.transport.update_presence()
            except Exception: pass
            await asyncio.sleep(60)

    def run(self):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(self.initialize())

        def run_loop(l):
            asyncio.set_event_loop(l)
            l.run_forever()

        t = threading.Thread(target=run_loop, args=(loop,), daemon=True)
        t.start()

        asyncio.run_coroutine_threadsafe(self.start_engine(loop), loop)

        self.ui.run()

        loop.call_soon_threadsafe(loop.stop)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, default=None, help="Bind direct listener to this port (default: auto)")
    args = parser.parse_args()

    try:
        app = CLIApp(listen_port=args.listen_port)
        app.run()
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")

if __name__ == "__main__":
    main()

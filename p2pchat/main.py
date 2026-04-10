from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from .config import ConfigManager
from .crypto import CryptoManager
from .engine import Engine
from .identity import load_or_create
from .transport import TransportManager


class CLIApp:
    def __init__(self, username: str | None = None, home: Path | None = None):
        self.home = home or (Path.home() / ".p2pchat")
        self.identity = load_or_create(self.home, username=username)
        self.config_mgr = ConfigManager(self.home)
        self.config = self.config_mgr.load()
        self.transport = TransportManager(self.config, self.identity)
        self.crypto = CryptoManager(self.identity, known_peers_path=self.home / "known_peers.json", plugin_dir=self.home / "plugins")
        self.engine = Engine(identity=self.identity, config=self.config, transport=self.transport, crypto=self.crypto)
        self.start_time = datetime.now()
        self.ignored_peers: set[str] = set()
        self.active_peer: str | None = None
        self.recent_chats: list[str] = []

    async def initialize(self):
        self.transport.set_on_message(self._on_incoming_message)
        await self.transport.initialize()

    def _on_incoming_message(self, msg: dict) -> None:
        parsed = self.engine.on_message(msg)
        from_id = parsed.get("from_id", "")
        if from_id in self.ignored_peers:
            return
        from_name = parsed.get("from_name") or "unknown"
        target = parsed.get("channel") or parsed.get("to") or "*"
        text = parsed.get("plaintext") or parsed.get("body") or ""
        ts = datetime.fromtimestamp(parsed.get("ts") or datetime.now().timestamp()).strftime("%H:%M:%S")
        print(f"\n[{ts}] {from_name} -> {target}: {text}")

    def print_banner(self):
        print("=" * 60)
        print(f"Welcome to Hermes, {self.identity.username}!")
        print(f"Peer ID: {self.identity.peer_id}")
        print(f"Transport mode: {self.config.transport_mode}")
        print("Use the menu or type /help for commands.")
        print("=" * 60)

    def print_help(self):
        print(
            """
Menu actions:
  1  Recent chats
  2  New contact
  3  Join channel
  4  Settings
  5  Status
  6  Advanced commands
  7  Quit

Advanced slash commands:
/peers                 List peers
/connect <peer>        Switch active direct target
/join #chan            Join channel
/leave #chan           Leave channel
/broadcast <txt>       Send to everyone
/mode <mode>           Change transport
/crypto <mode>         Change encryption
/key <peer> <key>      Set peer RSA public key or Fernet key
/plugin list           List plugins
/status                Show status
/history               Show last 20 msgs
/whoami                Show identity
/save                  Save config
/reload                Reload config/plugins
/exportlog <file>      Export chat log
/ping <peer|*>         Send ping message
/ignore <peer>         Mute peer output
/unignore <peer>       Unmute peer output
/clear                 Clear screen
/me <action>           Emote to broadcast
/quit                  Exit
"""
        )

    def _record_chat(self, target: str) -> None:
        if target and target not in self.recent_chats:
            self.recent_chats.insert(0, target)
            self.recent_chats = self.recent_chats[:10]

    async def _menu_recent_chats(self) -> None:
        if not self.recent_chats:
            print("No recent chats yet.")
            return
        for idx, chat in enumerate(self.recent_chats, start=1):
            print(f"{idx}. {chat}")
        choice = input("Pick a chat or press Enter to go back: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(self.recent_chats):
            self.active_peer = self.recent_chats[int(choice) - 1]
            print(f"Active chat: {self.active_peer}")

    async def _menu_new_contact(self) -> None:
        target = input("Peer name, ID, or IP: ").strip()
        if not target:
            return
        self.active_peer = target
        self._record_chat(target)
        print(f"Active contact: {target}")

    async def _menu_join_channel(self) -> None:
        channel = input("Channel name (like #dev): ").strip()
        if not channel:
            return
        await self.engine.join_channel(channel)
        self._record_chat(channel)
        print(f"Joined {channel}")

    async def _menu_settings(self) -> None:
        while True:
            print("\nSettings")
            print(f"1. Username: {self.identity.username}")
            print(f"2. Transport mode: {self.config.transport_mode}")
            print(f"3. Encryption mode: {self.config.crypto.default_mode}")
            print(f"4. Firebase queue enabled: {self.config.cloud.enabled}")
            print("5. Back")
            choice = input("Select: ").strip()
            if choice == "1":
                new_name = input("New username: ").strip()
                if new_name:
                    self.identity.username = new_name
                    print("Username updated for this session.")
            elif choice == "2":
                mode = input("Transport mode (fallback/all_p2p/all_relay/direct_only): ").strip()
                if mode in ("fallback", "all_p2p", "all_relay", "direct_only"):
                    self.config.transport_mode = mode
                    self.config_mgr.save(self.config)
                    print("Transport mode saved.")
            elif choice == "3":
                mode = input("Encryption (none/fernet/rsa/plugin-name): ").strip()
                if mode:
                    self.config.crypto.default_mode = mode
                    self.config_mgr.save(self.config)
                    print("Encryption saved.")
            elif choice == "4":
                self.config.cloud.enabled = not self.config.cloud.enabled
                self.config_mgr.save(self.config)
                print(f"Firebase queue enabled = {self.config.cloud.enabled}")
            elif choice == "5":
                return

    async def _menu_status(self) -> None:
        uptime = datetime.now() - self.start_time
        hermes_connected = self.transport.hermes.writer is not None
        print("\nStatus:")
        print(f"  Transport: {self.config.transport_mode}")
        print(f"  Crypto: {self.config.crypto.default_mode}")
        print(f"  Active chat: {self.active_peer or self.engine.active_channel or '(none)'}")
        print(f"  Hermes connected: {hermes_connected}")
        print(f"  Firebase queue: {self.config.cloud.enabled}")
        print(f"  Uptime: {uptime}")

    async def handle_command(self, line: str):
        line = line.strip()
        if not line:
            return True

        if line == "/quit":
            return False
        if line == "/help":
            self.print_help()
            return True

        if line == "/peers":
            peers_list = list(self.engine.peers.values())
            if not peers_list:
                print("No peers discovered yet.")
            else:
                for peer in peers_list:
                    print(f"  {peer.name} ({peer.peer_id})")
            return True

        if line.startswith("/connect "):
            target = line.split(" ", 1)[1].strip()
            self.active_peer = target
            self._record_chat(target)
            self.engine.active_channel = None
            print(f"Active direct target: {target}")
            return True

        if line.startswith("/join "):
            channel = line.split(" ", 1)[1].strip()
            await self.engine.join_channel(channel)
            self._record_chat(channel)
            print(f"Joined {channel}")
            return True

        if line.startswith("/leave "):
            channel = line.split(" ", 1)[1].strip()
            await self.engine.leave_channel(channel)
            print(f"Left {channel}")
            return True

        if line.startswith("/broadcast "):
            text = line.split(" ", 1)[1]
            await self.engine.broadcast(text)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.identity.username} -> * {text}")
            return True

        if line.startswith("/mode "):
            mode = line.split(" ", 1)[1].strip()
            if mode in ("fallback", "all_p2p", "all_relay", "direct_only"):
                self.config.transport_mode = mode
                self.config_mgr.save(self.config)
                print(f"Transport mode changed to: {mode}")
            else:
                print(f"Unknown mode: {mode}")
            return True

        if line.startswith("/crypto "):
            enc_mode = line.split(" ", 1)[1].strip()
            self.config.crypto.default_mode = enc_mode
            self.config_mgr.save(self.config)
            print(f"Crypto mode changed to: {enc_mode}")
            return True

        if line.startswith("/key "):
            parts = line.split(" ", 2)
            if len(parts) < 3:
                print("Usage: /key <peer_id> <rsa-public-key-or-fernet-key>")
                return True
            peer_id, key_material = parts[1], parts[2]
            try:
                key_kind = self.crypto.set_peer_key(peer_id, key_material)
                print(f"Stored {key_kind} key for {peer_id}")
            except ValueError as exc:
                print(f"Invalid key: {exc}")
            return True

        if line == "/plugin list":
            if not self.crypto.plugins:
                print("No crypto plugins loaded.")
            else:
                for plugin in self.crypto.plugins:
                    print(f"  {plugin.name}")
            return True

        if line == "/status":
            await self._menu_status()
            return True

        if line == "/clear":
            import os

            os.system("clear")
            return True

        if line.startswith("/me "):
            action = line[4:]
            await self.engine.broadcast(f"* {self.identity.username} {action}")
            print(f"* {self.identity.username} {action}")
            return True

        if line == "/history":
            for msg in self.engine.history[-20:]:
                ts = datetime.fromtimestamp(msg.get("ts", 0)).strftime("%H:%M:%S")
                text = msg.get("plaintext") or msg.get("body", "")
                print(f"[{ts}] {msg.get('from_name', '?')} -> {msg.get('to', '*')}: {text}")
            return True

        if line == "/whoami":
            print(f"Username: {self.identity.username}\nPeer ID: {self.identity.peer_id}\nConfig: {self.config}")
            return True

        if line == "/save":
            self.config_mgr.save(self.config)
            print("Config saved.")
            return True

        if line == "/reload":
            self.config = self.config_mgr.load()
            self.transport.config = self.config
            self.crypto = CryptoManager(
                self.identity,
                known_peers_path=self.home / "known_peers.json",
                plugin_dir=self.home / "plugins",
            )
            self.engine.config = self.config
            self.engine.crypto = self.crypto
            print("Config and plugins reloaded.")
            return True

        if line.startswith("/exportlog "):
            fname = line.split(" ", 1)[1].strip()
            with open(fname, "w", encoding="utf-8") as f:
                for msg in self.engine.history:
                    f.write(str(msg) + "\n")
            print(f"Exported chat log to {fname}")
            return True

        if line.startswith("/ping "):
            target = line.split(" ", 1)[1].strip() or "*"
            await self.engine.broadcast(f"ping:{target}:{datetime.now().timestamp()}")
            print(f"Sent ping to {target}")
            return True

        if line.startswith("/ignore "):
            peer = line.split(" ", 1)[1].strip()
            self.ignored_peers.add(peer)
            print(f"Ignoring {peer}")
            return True

        if line.startswith("/unignore "):
            peer = line.split(" ", 1)[1].strip()
            self.ignored_peers.discard(peer)
            print(f"Unignored {peer}")
            return True

        if self.active_peer:
            await self.engine.send_direct(self.active_peer, line)
            self._record_chat(self.active_peer)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.identity.username} -> {self.active_peer}: {line}")
        elif self.engine.active_channel:
            await self.engine.send_channel(self.engine.active_channel, line)
            self._record_chat(self.engine.active_channel)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.identity.username} -> {self.engine.active_channel} {line}")
        else:
            await self.engine.broadcast(line)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.identity.username} -> * {line}")

        return True

    async def run_menu(self):
        await self.initialize()
        self.print_banner()
        while True:
            print("\nMain Menu")
            print("1. Recent chats")
            print("2. New contact")
            print("3. Join channel")
            print("4. Settings")
            print("5. Status")
            print("6. Advanced commands")
            print("7. Quit")
            choice = input("Select: ").strip()
            if choice == "1":
                await self._menu_recent_chats()
            elif choice == "2":
                await self._menu_new_contact()
            elif choice == "3":
                await self._menu_join_channel()
            elif choice == "4":
                await self._menu_settings()
            elif choice == "5":
                await self._menu_status()
            elif choice == "6":
                self.print_help()
                while True:
                    line = input("> ")
                    if line.strip() == "/back":
                        break
                    try:
                        should_continue = await self.handle_command(line)
                        if should_continue is False:
                            return
                    except Exception as e:
                        print(f"Error: {e}")
            elif choice == "7":
                return

    async def run(self):
        await self.run_menu()


async def amain(username: str | None = None, home: Path | None = None):
    app = CLIApp(username=username, home=home)
    await app.run()


def main():
    parser = argparse.ArgumentParser(description="P2PChat terminal client")
    parser.add_argument("--username", help="Your display name")
    parser.add_argument("--home", help="Override config directory for state files")
    args = parser.parse_args()
    try:
        asyncio.run(amain(args.username, Path(args.home) if args.home else None))
    except KeyboardInterrupt:
        print("\nGoodbye!")


if __name__ == "__main__":
    main()

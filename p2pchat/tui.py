from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, RichLog, Static

from .config import ConfigManager
from .crypto import CryptoManager
from .engine import Engine
from .identity import load_or_create
from .transport import TransportManager


@dataclass
class PeerEntry:
    peer_id: str
    name: str
    online: bool = False


class HermesTUI(App):
    CSS = """
    Screen {
      background: #111318;
      color: #e8ecf2;
    }

    #login-layer {
      layer: overlay;
      align: center middle;
      background: rgba(0, 0, 0, 0.70);
    }

    #login-card {
      width: 74;
      border: round #3a4660;
      background: #171c25;
      padding: 1 2;
    }

    #login-title {
      text-style: bold;
      color: #dce7ff;
      margin-bottom: 1;
    }

    #login-hint {
      color: #9fb0cc;
      margin-bottom: 1;
    }

    .login-input {
      margin-bottom: 1;
    }

    #root {
      height: 1fr;
      layout: horizontal;
    }

    #left {
      width: 38;
      border-right: solid #273142;
      background: #121820;
      layout: vertical;
      padding: 1;
    }

    .panel-title {
      color: #cfe0ff;
      text-style: bold;
      margin-top: 1;
      margin-bottom: 1;
    }

    #channel-list, #peer-list {
      height: 1fr;
      border: round #2f3b52;
      background: #151c27;
      margin-bottom: 1;
    }

    #right {
      width: 1fr;
      layout: vertical;
      background: #0f141d;
    }

    #status {
      height: 3;
      border-bottom: solid #273142;
      background: #121a24;
      padding: 0 1;
      color: #9fb0cc;
    }

    #messages {
      height: 1fr;
      border: round #2f3b52;
      background: #101722;
      margin: 1;
      padding: 0 1;
    }

    #actions {
      height: 3;
      padding: 0 1;
      background: #111823;
      border-top: solid #273142;
      align-vertical: middle;
    }

    .action-btn {
      min-width: 16;
      margin-right: 1;
    }

    #composer {
      height: 4;
      padding: 0 1 1 1;
      background: #111823;
      border-top: solid #273142;
    }

    #input {
      width: 1fr;
      margin-right: 1;
    }

    #send {
      min-width: 12;
      background: #2f66ff;
      color: #ffffff;
    }

    ListItem {
      color: #e8ecf2;
    }

    ListItem.--highlight {
      background: #20304a;
      color: #ffffff;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "focus_input", "Input"),
        ("ctrl+b", "goto_broadcast", "Broadcast"),
    ]

    active_target = reactive("@broadcast")

    def __init__(self, home: Path | None = None, listen_port: int | None = None):
        super().__init__()
        self.home = home or (Path.home() / ".p2pchat")
        self.listen_port = listen_port

        self.identity = load_or_create(self.home)
        self.config_mgr = ConfigManager(self.home)
        self.config = self.config_mgr.load()
        self.transport = TransportManager(self.config, self.identity)
        self.crypto = CryptoManager(self.identity)
        self.engine = Engine(self.identity, self.config, self.transport, self.crypto)

        self.engine.set_ui(self)
        self.transport.set_on_message(self.engine.on_message)

        self._messages: dict[str, list[dict[str, Any]]] = {"@broadcast": []}
        self._message_states: dict[str, str] = {}
        self._known_peers: dict[str, PeerEntry] = {}
        self._known_channels: list[str] = ["@broadcast"]
        self._auto_load_limit = 120

        self._bootstrap_task: asyncio.Task | None = None
        self._presence_task: asyncio.Task | None = None
        self._target_by_item_id: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="root"):
            with Vertical(id="left"):
                yield Label("Channels", classes="panel-title")
                yield ListView(id="channel-list")
                yield Label("Peers", classes="panel-title")
                yield ListView(id="peer-list")
            with Vertical(id="right"):
                yield Static("Connecting...", id="status")
                yield RichLog(id="messages", wrap=True, markup=True, auto_scroll=True)
                with Horizontal(id="actions"):
                    yield Button("Broadcast", id="btn-broadcast", classes="action-btn")
                    yield Button("Sync Now", id="btn-sync", classes="action-btn")
                    yield Button("Peers", id="btn-peers", classes="action-btn")
                    yield Button("Help", id="btn-help", classes="action-btn")
                with Horizontal(id="composer"):
                    yield Input(placeholder="Type a message or command (/help)", id="input")
                    yield Button("Send", id="send")
        with Vertical(id="login-layer"):
            with Vertical(id="login-card"):
                yield Label("Hermes TUI", id="login-title")
                yield Label("Sign in and join a target to start chatting", id="login-hint")
                yield Input(placeholder="Username", id="login-username", classes="login-input")
                yield Input(password=True, placeholder="Password", id="login-password", classes="login-input")
                yield Input(placeholder="Start target (@broadcast or peer id)", value="@broadcast", id="login-target", classes="login-input")
                yield Input(placeholder="Listen port (optional)", id="login-port", classes="login-input")
                yield Button("Connect", id="login-connect")
        yield Footer()

    async def on_mount(self) -> None:
        self._rebuild_channel_list()
        self._rebuild_peer_list()
        self.query_one("#login-username", Input).focus()

    async def action_focus_input(self) -> None:
        if self.query_one("#login-layer", Vertical).display:
            return
        self.query_one("#input", Input).focus()

    async def action_goto_broadcast(self) -> None:
        if self.query_one("#login-layer", Vertical).display:
            return
        await self._switch_target("@broadcast")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "login-connect":
            await self._connect_from_login()
            return
        if bid == "send":
            await self._send_current_input()
            return
        if bid == "btn-broadcast":
            await self._switch_target("@broadcast")
            return
        if bid == "btn-sync":
            await self._sync_active_target()
            return
        if bid == "btn-peers":
            await self._show_online_peers()
            return
        if bid == "btn-help":
            self._add_system("Commands: /help /join @chan /connect <peer_id> /peers /status /load [n]")
            return

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "input":
            await self._send_current_input()
        elif event.input.id in {"login-username", "login-password", "login-target", "login-port"}:
            await self._connect_from_login()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = (event.item.id or "").strip()
        if not item_id:
            return
        target = self._target_by_item_id.get(item_id)
        if target:
            await self._switch_target(target)

    async def _connect_from_login(self) -> None:
        username = self.query_one("#login-username", Input).value.strip()
        password = self.query_one("#login-password", Input).value.strip()
        target = self.query_one("#login-target", Input).value.strip() or "@broadcast"
        port_text = self.query_one("#login-port", Input).value.strip()

        if not username or not password:
            self._set_status("Enter username and password.")
            return

        if self._bootstrap_task and not self._bootstrap_task.done():
            return

        self._set_status("Connecting...")
        self._bootstrap_task = asyncio.create_task(
            self._bootstrap(username=username, password=password, target=target, port_text=port_text)
        )
        await self._bootstrap_task

    async def _bootstrap(self, username: str, password: str, target: str, port_text: str) -> None:
        target = self._normalize_target(target)
        if port_text:
            try:
                self.listen_port = int(port_text)
            except ValueError:
                self._set_status("Invalid listen port.")
                return

        ok = await self.transport.initialize(listen_port=self.listen_port)
        if not ok:
            self._set_status("Failed to initialize transport.")
            return

        auth = await self.transport.authenticate(username, password)
        if not auth.get("ok"):
            self._set_status(f"Auth failed: {auth.get('error', 'unknown error')}")
            return

        self.identity.peer_id = auth["peer_id"]
        self.identity.username = auth["username"]
        self.engine.identity.peer_id = auth["peer_id"]
        self.engine.identity.username = auth["username"]

        self.transport.update_presence()
        await self.transport.start_personal_inbox_listener()

        if target.startswith("@"):
            await self.engine.join_channel(target)
            self._remember_channel(target)
        else:
            self._remember_peer(target, target, online=True)

        self.query_one("#login-layer", Vertical).display = False
        self.query_one("#input", Input).focus()

        await self._switch_target(target)
        self._set_status(self._status_line())

        if self._presence_task and not self._presence_task.done():
            self._presence_task.cancel()
        self._presence_task = asyncio.create_task(self._background_loops())

        self._add_system("Connected. Textual UI is active.")

    async def _background_loops(self) -> None:
        try:
            while True:
                try:
                    self.transport.update_presence()
                    await self._refresh_online_peers()
                    await self.transport.load_new_messages(self.active_target, limit=self._auto_load_limit)
                    self._set_status(self._status_line())
                except Exception as exc:
                    self._set_status(f"Sync warning: {exc}")
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    def _status_line(self) -> str:
        s = self.transport.status
        user = self.identity.username or "anonymous"
        pid = (self.identity.peer_id or "unknown")[:8]
        net = s.get("last_transport") or "idle"
        direct = s.get("direct_port") or "-"
        udp = s.get("udp_port") or "-"
        return f"{user} ({pid}) | target={self.active_target} | direct={direct} udp={udp} | transport={net}"

    async def _show_online_peers(self) -> None:
        peers = self.transport.list_online_peers()
        if not peers:
            self._add_system("No peers online.")
            return
        line = ", ".join(f"{p['name']}:{p['id'][:8]}" for p in peers[:30])
        self._add_system(f"Online peers: {line}")

    async def _refresh_online_peers(self) -> None:
        peers = self.transport.list_online_peers()
        as_ui = []
        for p in peers:
            as_ui.append({"id": p["id"], "name": p.get("name") or p["id"], "online": True})
        self.update_peers(as_ui)

    def _target_for_message(self, msg: dict[str, Any]) -> str:
        channel = msg.get("channel")
        if isinstance(channel, str) and channel:
            return channel

        from_id = str(msg.get("from_id") or "")
        to = str(msg.get("to") or "")
        me = str(self.identity.peer_id or "")

        if from_id and from_id != me:
            return from_id
        if to and to != "*" and to != "@broadcast" and to != me:
            return to
        return "@broadcast"

    def _normalize_target(self, target: str) -> str:
        t = str(target or "").strip()
        if t.startswith("@"):
            return "@" + t[1:].lower()
        return t

    def push_message(self, msg: dict):
        target = self._target_for_message(msg)
        msgs = self._messages.setdefault(target, [])
        msg_id = msg.get("id")
        if msg_id:
            for existing in msgs:
                if existing.get("id") == msg_id:
                    existing.update(msg)
                    if target == self.active_target:
                        self._render_messages_for_active_target()
                    return
        msgs.append(dict(msg))
        msgs.sort(key=lambda m: float(m.get("ts") or time.time()))
        if len(msgs) > 2500:
            self._messages[target] = msgs[-2500:]

        if target.startswith("@"):
            self._remember_channel(target)
        else:
            name = str(msg.get("from_name") or target)
            self._remember_peer(target, name, online=True)

        if msg.get("id"):
            self._message_states[str(msg["id"])] = str(msg.get("state") or "sent")

        if target == self.active_target:
            self._render_messages_for_active_target()

    def update_peers(self, peers: list):
        for p in peers:
            pid = str(p.get("id") or "")
            if not pid:
                continue
            name = str(p.get("name") or pid)
            online = bool(p.get("online", False))
            self._remember_peer(pid, name, online=online)

    def set_message_state(self, msg_id: str, state: str):
        self._message_states[msg_id] = state
        msgs = self._messages.get(self.active_target, [])
        changed = False
        for m in msgs:
            if str(m.get("id") or "") == msg_id:
                m["state"] = state
                changed = True
                break
        if changed:
            self._render_messages_for_active_target()

    def _remember_channel(self, channel: str) -> None:
        if channel not in self._known_channels:
            self._known_channels.append(channel)
            self._known_channels.sort(key=lambda c: (0 if c == "@broadcast" else 1, c.lower()))
            self._rebuild_channel_list()

    def _remember_peer(self, peer_id: str, name: str, online: bool) -> None:
        existing = self._known_peers.get(peer_id)
        if existing:
            existing.name = name or existing.name
            existing.online = online
        else:
            self._known_peers[peer_id] = PeerEntry(peer_id=peer_id, name=name or peer_id, online=online)
        self._rebuild_peer_list()

    def _rebuild_channel_list(self) -> None:
        self._target_by_item_id = {
            k: v for k, v in self._target_by_item_id.items() if k.startswith("peer_")
        }
        self.call_later(self._async_rebuild_channel_list)

    async def _async_rebuild_channel_list(self) -> None:
        try:
            lv = self.query_one("#channel-list", ListView)
            await lv.clear()
            self._target_by_item_id = {
                k: v for k, v in self._target_by_item_id.items() if k.startswith("peer_")
            }
            for idx, ch in enumerate(self._known_channels, start=1):
                marker = "#" if ch.startswith("@") else ""
                title = f"{marker}{ch.lstrip('@')}"
                item_id = self._make_item_id("channel", idx, ch)
                self._target_by_item_id[item_id] = ch
                lv.append(ListItem(Label(title), id=item_id))
        except Exception:
            pass

    def _rebuild_peer_list(self) -> None:
        self._target_by_item_id = {
            k: v for k, v in self._target_by_item_id.items() if k.startswith("channel_")
        }
        self.call_later(self._async_rebuild_peer_list)

    async def _async_rebuild_peer_list(self) -> None:
        try:
            lv = self.query_one("#peer-list", ListView)
            await lv.clear()
            self._target_by_item_id = {
                k: v for k, v in self._target_by_item_id.items() if k.startswith("channel_")
            }
            peers = sorted(self._known_peers.values(), key=lambda p: p.name.lower())
            for idx, p in enumerate(peers, start=1):
                dot = "[online]" if p.online else "[offline]"
                item_id = self._make_item_id("peer", idx, p.peer_id)
                self._target_by_item_id[item_id] = p.peer_id
                lv.append(ListItem(Label(f"{dot} {p.name} ({p.peer_id[:8]})"), id=item_id))
        except Exception:
            pass

    def _make_item_id(self, prefix: str, idx: int, raw: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(raw))
        if not safe or safe[0].isdigit():
            safe = f"x_{safe}"
        return f"{prefix}_{idx}_{safe[:40]}"

    def _fmt_msg(self, msg: dict[str, Any]) -> str:
        ts = float(msg.get("ts") or time.time())
        ts_text = time.strftime("%H:%M:%S", time.localtime(ts))
        body = str(msg.get("body") or "")
        msg_type = str(msg.get("type") or "")

        if msg_type == "system":
            return f"[dim]{ts_text}  {body}[/dim]"

        from_name = str(msg.get("from_name") or msg.get("from_id") or "unknown")
        mid = str(msg.get("id") or "")
        state = self._message_states.get(mid) or str(msg.get("state") or "")
        state_tag = f" [dim]({state})[/dim]" if state else ""

        me = str(self.identity.peer_id or "")
        from_id = str(msg.get("from_id") or "")
        if from_id and from_id == me:
            return f"[bold #7fb3ff]{ts_text}  You:[/bold #7fb3ff] {body}{state_tag}"
        return f"[bold #c3d3ee]{ts_text}  {from_name}:[/bold #c3d3ee] {body}"

    def _render_messages_for_active_target(self) -> None:
        log = self.query_one("#messages", RichLog)
        log.clear()
        for msg in self._messages.get(self.active_target, []):
            log.write(self._fmt_msg(msg), scroll_end=True)

    async def _switch_target(self, target: str) -> None:
        target = self._normalize_target(target)
        if not target:
            return
        self.active_target = target
        if target.startswith("@"):
            await self.engine.join_channel(target)
            self._remember_channel(target)
        else:
            self._remember_peer(target, self._known_peers.get(target, PeerEntry(target, target)).name, online=True)

        self._messages.setdefault(target, [])
        await self.transport.load_new_messages(target, limit=self._auto_load_limit)
        self._render_messages_for_active_target()
        self._set_status(self._status_line())

    async def _sync_active_target(self) -> None:
        count = await self.transport.load_new_messages(self.active_target, limit=self._auto_load_limit)
        self._set_status(f"Synced {count} new messages for {self.active_target}")

    async def _send_current_input(self) -> None:
        inp = self.query_one("#input", Input)
        text = inp.value.strip()
        if not text:
            return
        inp.value = ""

        if text.startswith("/"):
            await self._run_command(text)
            return

        mid = str(uuid.uuid4())
        local = {
            "id": mid,
            "from_id": self.identity.peer_id,
            "from_name": self.identity.username,
            "ts": time.time(),
            "body": text,
            "state": "sending",
            "channel": self.active_target if self.active_target.startswith("@") else None,
            "to": self.active_target,
        }
        self.push_message(local)

        if self.active_target == "@broadcast":
            await self.engine.broadcast(text, msg_id=mid)
        elif self.active_target.startswith("@"):
            await self.engine.send_channel(self.active_target, text, msg_id=mid)
        else:
            await self.engine.send_direct(self.active_target, text, msg_id=mid)

    async def _run_command(self, text: str) -> None:
        parts = text.strip().split()
        cmd = parts[0].lower()

        if cmd == "/help":
            self._add_system("/join @chan | /connect <peer_id> | /peers | /status | /load [n] | /clear | /quit")
            return
        if cmd == "/quit":
            self.exit()
            return
        if cmd == "/clear":
            self._messages[self.active_target] = []
            self._render_messages_for_active_target()
            return
        if cmd == "/join" and len(parts) > 1:
            chan = parts[1]
            if not chan.startswith("@"):
                chan = "@" + chan
            chan = self._normalize_target(chan)
            await self._switch_target(chan)
            return
        if cmd == "/connect" and len(parts) > 1:
            await self._switch_target(parts[1])
            return
        if cmd == "/peers":
            await self._show_online_peers()
            return
        if cmd == "/status":
            self._add_system(self._status_line())
            return
        if cmd == "/load":
            if len(parts) > 1:
                try:
                    self._auto_load_limit = max(1, min(500, int(parts[1])))
                except ValueError:
                    self._add_system("Usage: /load [1-500]")
                    return
            count = await self.transport.load_new_messages(self.active_target, limit=self._auto_load_limit)
            self._add_system(f"Loaded {count} new messages.")
            return

        self._add_system(f"Unknown command: {cmd}")

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def _add_system(self, text: str) -> None:
        self.push_message({
            "id": f"sys-{uuid.uuid4()}",
            "type": "system",
            "body": text,
            "ts": time.time(),
            "channel": self.active_target if self.active_target.startswith("@") else None,
            "to": self.active_target,
        })

    async def on_shutdown_request(self) -> None:
        if self._presence_task and not self._presence_task.done():
            self._presence_task.cancel()


def run_textual_tui(listen_port: int | None = None) -> None:
    HermesTUI(listen_port=listen_port).run()

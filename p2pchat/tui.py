from __future__ import annotations

import asyncio
import time
import uuid
import re
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from .config import ConfigManager
from .crypto import CryptoManager
from .engine import Engine
from .identity import load_or_create
from .transport import TransportManager


class HermesTUI(App):
    CSS = """
    Screen { background: #0b0d12; color: #e1e4eb; }
    #root { height: 1fr; layout: horizontal; }
    #left { width: 34; border-right: solid #1c212b; background: #0e1117; layout: vertical; padding: 1; }
    #left.hidden { display: none; }
    .panel-title { color: #3b82f6; text-style: bold; margin: 1 0; padding-left: 1; }
    #messages { height: 1fr; border: round #1c212b; background: #080a0f; margin: 1; padding: 0 1; }
    #status { height: 3; padding: 0 2; color: #64748b; border-bottom: solid #1c212b; content-align: middle left; }
    #composer { height: 4; padding: 0 1 1 1; background: #0b0d12; }
    Input { border: tall #1c212b; background: #0b0d12; color: #f0f2f5; }
    Input:focus { border: tall #3b82f6; }
    ListItem { padding: 0 1; margin: 0 1; border-radius: 4; }
    ListItem.--highlight { background: #1e293b; color: #3b82f6; text-style: bold; }
    #chat-list { background: transparent; border: none; }
    """

    CSS_HIGH_CONTRAST = """
    Screen { background: #000000; color: #ffffff; }
    #left { background: #000000; border-right: solid #ffffff; }
    .panel-title { color: #ffffff; text-style: bold; }
    #messages { background: #000000; border: round #ffffff; }
    #status { color: #ffffff; border-bottom: solid #ffffff; }
    #composer { background: #000000; }
    Input { border: tall #ffffff; background: #000000; color: #ffffff; }
    Input:focus { border: tall #ffffff; }
    ListItem.--highlight { background: #ffffff; color: #000000; text-style: bold; }
    """

    def on_unmount(self):
        try:
            if self.transport:
                if getattr(self.transport, "direct_peer", None):
                    asyncio.create_task(self.transport.direct_peer.close_udp())
        except Exception:
            pass

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "focus_input", "Focus Input"),
        ("ctrl+s", "toggle_sidebar", "Toggle Sidebar"),
        ("alt+up", "prev_chat", "Previous"),
        ("alt+down", "next_chat", "Next"),
        ("ctrl+n", "new_channel", "New Channel"),
        ("ctrl+r", "rename_channel", "Rename Channel"),
        ("ctrl+d", "delete_channel", "Delete Channel"),
        ("ctrl+t", "toggle_contrast", "Toggle Contrast"),
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
        self.engine = Engine(
            self.identity, self.config, self.transport, self.crypto, home=self.home
        )
        self.engine.set_ui(self)
        self.transport.set_on_message(self.engine.on_message)

        self._known_peers = {}
        self._known_channels = ["@broadcast"]
        self._last_input_ts = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="root"):
            with Vertical(id="left"):
                yield Label("CONVERSATIONS", classes="panel-title")
                yield ListView(id="chat-list")
            with Vertical(id="right"):
                yield Static("Connecting to Hermes...", id="status")
                yield RichLog(id="messages", wrap=True, markup=True, auto_scroll=True)
                with Horizontal(id="composer"):
                    yield Input(placeholder="Type message or /search query", id="input")
        yield Footer()

    async def on_mount(self):
        self._rebuild_list()
        self.query_one("#input").focus()
        if getattr(self.config.ui, "high_contrast", False):
            self.stylesheet.add_source(self.CSS_HIGH_CONTRAST)
        await self.transport.initialize(listen_port=getattr(self, "listen_port", None))
        try:
            chans = await self.transport.list_channels()
            for c in chans:
                if c not in self._known_channels:
                    self._known_channels.append(c)
            self._known_channels.sort()
            self._rebuild_list()
        except Exception:
            pass
        self.set_interval(1.0, self._update_status_line)
        if self.config.ui.last_channel and self.config.ui.last_channel != "@broadcast":
            await self._switch_target(self.config.ui.last_channel)

    def _update_status_line(self):
        s = self.transport.status
        self.query_one("#status").update(
            f"👤 {self.identity.username} | 🎯 {self.active_target} | ⚡ {s.get('last_transport', 'relay')} | Ctrl+N/R/D channel"
        )

    def action_toggle_sidebar(self):
        self.query_one("#left").toggle_class("hidden")

    def action_toggle_contrast(self):
        self.config.ui.high_contrast = not getattr(
            self.config.ui, "high_contrast", False
        )
        try:
            self.config_mgr.save(self.config)
        except Exception:
            pass
        if self.config.ui.high_contrast:
            self.stylesheet.add_source(self.CSS_HIGH_CONTRAST)
        else:
            self.stylesheet.reset()
            self.stylesheet.add_source(self.CSS)

    async def action_prev_chat(self):
        await self._cycle_chat(-1)

    async def action_next_chat(self):
        await self._cycle_chat(1)

    def action_focus_input(self):
        self.query_one("#input").focus()

    async def _cycle_chat(self, delta: int):
        all_chats = self._known_channels + list(self._known_peers.keys())
        if not all_chats:
            return
        idx = (all_chats.index(self.active_target) + delta) % len(all_chats)
        await self._switch_target(all_chats[idx])

    async def _switch_target(self, target: str):
        self.active_target = target
        log = self.query_one("#messages", RichLog)
        log.clear()
        msgs = self.engine.storage.get_messages(target)
        for m in msgs:
            self.push_message(m)
        if self.config.ui.last_channel != target:
            self.config.ui.last_channel = target
            try:
                self.config_mgr.save(self.config)
            except Exception:
                pass
        self._rebuild_list()

    async def action_new_channel(self):
        name = await self.push_screen_wait(
            _ChannelPrompt("Create Channel", "Channel name (example: @dev)")
        )
        if not name:
            return
        if not name.startswith("@"):
            name = "@" + name
        res = await self.transport.create_channel(name)
        if res.get("ok"):
            if name not in self._known_channels:
                self._known_channels.append(name)
                self._known_channels.sort()
            await self._switch_target(name)
            self.push_message({"body": f"Channel created: {name}", "from_name": "SYS"})
        else:
            self.push_message({"body": f"Create failed: {name}", "from_name": "SYS"})

    async def action_rename_channel(self):
        if not self.active_target.startswith("@") or self.active_target == "@broadcast":
            self.push_message(
                {"body": "Pick a non-broadcast channel first.", "from_name": "SYS"}
            )
            return
        new_name = await self.push_screen_wait(
            _ChannelPrompt("Rename Channel", f"New name for {self.active_target}")
        )
        if not new_name:
            return
        if not new_name.startswith("@"):
            new_name = "@" + new_name
        old_name = self.active_target
        res = await self.transport.rename_channel(old_name, new_name)
        if res.get("ok"):
            if old_name in self._known_channels:
                self._known_channels.remove(old_name)
            if new_name not in self._known_channels:
                self._known_channels.append(new_name)
                self._known_channels.sort()
            await self._switch_target(new_name)
            self.push_message(
                {"body": f"Renamed {old_name} -> {new_name}", "from_name": "SYS"}
            )
        else:
            self.push_message(
                {"body": f"Rename failed: {old_name}", "from_name": "SYS"}
            )

    async def action_delete_channel(self):
        if not self.active_target.startswith("@") or self.active_target == "@broadcast":
            self.push_message(
                {"body": "Pick a non-broadcast channel first.", "from_name": "SYS"}
            )
            return
        target = self.active_target
        ok = await self.push_screen_wait(
            _ConfirmPrompt("Delete Channel", f"Delete {target}? This cannot be undone.")
        )
        if not ok:
            return
        res = await self.transport.delete_channel(target)
        if res.get("ok"):
            if target in self._known_channels:
                self._known_channels.remove(target)
            await self._switch_target("@broadcast")
            self.push_message(
                {"body": f"Deleted channel: {target}", "from_name": "SYS"}
            )
        else:
            self.push_message({"body": f"Delete failed: {target}", "from_name": "SYS"})

    def _rebuild_list(self):
        lv = self.query_one("#chat-list", ListView)
        lv.clear()
        for ch in self._known_channels:
            lv.append(ListItem(Label(f" # {ch}"), id=f"l-{ch}"))
        for p_id, p in self._known_peers.items():
            dot = "●" if getattr(p, "online", False) else "○"
            lv.append(ListItem(Label(f" {dot} {p.name}"), id=f"l-{p_id}"))

    def push_message(self, msg: dict):
        try:
            log = self.query_one("#messages", RichLog)
        except Exception:
            return
        is_me = msg.get("from_id") == self.identity.peer_id
        color = "3b82f6" if is_me else "94a3b8"
        name = "You" if is_me else msg.get("from_name", "unknown")
        lock = "🔒" if msg.get("enc") != "none" else ""
        ts = time.strftime("%H:%M", time.localtime(msg.get("ts", time.time())))
        log.write(f"[{color}]{ts} [bold]{name}[/bold] {lock}: {msg.get('body')}[/]")

    async def on_input_submitted(self, event: Input.Submitted):
        txt = event.value.strip()
        event.input.value = ""
        if not txt:
            return
        now = time.time()
        if now - self._last_input_ts < 0.2:
            return
        self._last_input_ts = now

        if txt.startswith("/create "):
            name = txt.split(maxsplit=1)[1].strip()
            if not name.startswith("@"):
                name = "@" + name
            if self.active_target == name:
                self.push_message({"body": f"Already in {name}", "from_name": "SYS"})
                return
            res = await self.transport.create_channel(name)
            if res.get("ok"):
                if name not in self._known_channels:
                    self._known_channels.append(name)
                    self._known_channels.sort()
                await self._switch_target(name)
                self.push_message(
                    {"body": f"Channel created: {name}", "from_name": "SYS"}
                )
            else:
                self.push_message(
                    {"body": f"Create failed: {name}", "from_name": "SYS"}
                )
            return

        if txt.startswith("/rename "):
            parts = txt.split()
            if len(parts) == 3:
                old_name = parts[1]
                new_name = parts[2]
                if not old_name.startswith("@"):
                    old_name = "@" + old_name
                if not new_name.startswith("@"):
                    new_name = "@" + new_name
                if old_name == new_name:
                    self.push_message(
                        {
                            "body": "Rename skipped: channel names match.",
                            "from_name": "SYS",
                        }
                    )
                    return
                res = await self.transport.rename_channel(old_name, new_name)
                if res.get("ok"):
                    if old_name in self._known_channels:
                        self._known_channels.remove(old_name)
                    if new_name not in self._known_channels:
                        self._known_channels.append(new_name)
                        self._known_channels.sort()
                    if self.active_target == old_name:
                        await self._switch_target(new_name)
                    self.push_message(
                        {
                            "body": f"Renamed {old_name} -> {new_name}",
                            "from_name": "SYS",
                        }
                    )
                else:
                    err = res.get("error") or "invalid channel name"
                    self.push_message(
                        {"body": f"Rename failed: {err}", "from_name": "SYS"}
                    )
            else:
                self.push_message(
                    {"body": "Usage: /rename @old @new", "from_name": "SYS"}
                )
            return

        if txt.startswith("/delete "):
            name = txt.split(maxsplit=1)[1].strip()
            if not name.startswith("@"):
                name = "@" + name
            if name == "@broadcast":
                self.push_message(
                    {"body": "Cannot delete @broadcast", "from_name": "SYS"}
                )
                return
            if "--yes" not in txt and " -y" not in txt:
                self.push_message(
                    {
                        "body": f"Confirm delete {name}: /delete {name} --yes",
                        "from_name": "SYS",
                    }
                )
                return
            res = await self.transport.delete_channel(name)
            if res.get("ok"):
                if name in self._known_channels:
                    self._known_channels.remove(name)
                if self.active_target == name:
                    await self._switch_target("@broadcast")
                self.push_message(
                    {"body": f"Deleted channel: {name}", "from_name": "SYS"}
                )
            else:
                self.push_message(
                    {"body": f"Delete failed: {name}", "from_name": "SYS"}
                )
            return

        if txt.startswith("/join "):
            name = txt.split(maxsplit=1)[1].strip()
            if name.startswith("#"):
                target = self.transport.resolve_direct_target(name)
                await self._switch_target(target)
                self.push_message(
                    {"body": f"Direct chat target set to {target}", "from_name": "SYS"}
                )
                return
            if not name.startswith("@"):
                name = "@" + name
            if self.active_target == name:
                self.push_message({"body": f"Already in {name}", "from_name": "SYS"})
                return
            await self.transport.join_channel(name)
            if name not in self._known_channels:
                self._known_channels.append(name)
                self._known_channels.sort()
            await self._switch_target(name)
            return

        if txt == "/channels":
            chans = await self.transport.list_channels()
            self.push_message(
                {
                    "body": "Channels: " + (", ".join(chans) if chans else "none"),
                    "from_name": "SYS",
                }
            )
            return

        if txt.startswith("/search "):
            query = txt[8:]
            results = self.engine.storage.search_messages(query)
            self.push_message(
                {
                    "body": f"--- SEARCH RESULTS FOR '{query}' ({len(results)} found) ---",
                    "from_name": "SYS",
                }
            )
            for r in results:
                self.push_message(r)
            return

        if txt == "/outbox":
            pending = self.engine.storage.list_outbox(limit=10)
            if not pending:
                self.push_message({"body": "Outbox empty.", "from_name": "SYS"})
            else:
                self.push_message(
                    {"body": f"Outbox pending: {len(pending)}", "from_name": "SYS"}
                )
                for m in pending:
                    target = m.get("to") or m.get("channel")
                    body = m.get("body") or ""
                    self.push_message(
                        {"body": f"- {target} {body}", "from_name": "SYS"}
                    )
            return

        if txt.startswith("/me "):
            action = txt[4:].strip()
            if not action:
                self.push_message({"body": "Usage: /me <action>", "from_name": "SYS"})
                return
            txt = f"* {self.identity.username} {action}"

        if self.active_target.startswith("@"):
            await self.engine.send_channel(self.active_target, txt)
        else:
            await self.engine.send_direct(self.active_target, txt)

    def update_peers(self, peers: list):
        for p in peers:
            self._known_peers[p["id"]] = type("Peer", (object,), p)
        self._rebuild_list()

    def set_message_state(self, mid, state):
        pass


def run_textual_tui(listen_port=None):
    HermesTUI(listen_port=listen_port).run()


from textual.screen import ModalScreen


class _ChannelPrompt(ModalScreen[str | None]):
    def __init__(self, title: str, placeholder: str):
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title)
            yield Input(placeholder=self._placeholder, id="channel-prompt")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="ok", variant="primary")

    def on_mount(self):
        self.query_one("#channel-prompt", Input).focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        value = self.query_one("#channel-prompt", Input).value.strip()
        self.dismiss(value or None)

    def on_input_submitted(self, event: Input.Submitted):
        value = event.value.strip()
        self.dismiss(value or None)


class _ConfirmPrompt(ModalScreen[bool]):
    def __init__(self, title: str, body: str):
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title)
            yield Static(self._body)
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Delete", id="confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "confirm")

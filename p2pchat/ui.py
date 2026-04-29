import curses
import threading
import time
import uuid
import asyncio
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Any
import logging

from .config import ConfigManager

VERSION = "0.3.0"


@dataclass
class PeerInfo:
    id: str
    name: str
    online: bool = False


class ChatUI:
    def __init__(self, identity, engine, config):
        self.identity = identity
        self.engine = engine
        self.config = config
        self.config_mgr = ConfigManager(Path.home() / ".p2pchat")
        self.messages = []
        self.peers = []
        self.input_buf = ""
        self.input_cursor = 0
        self.scroll_offset = 0
        self.active_channel = "@broadcast"
        self.auto_load_enabled = True
        self.auto_load_limit = 100
        self._loader_thread = None
        self._lock = threading.Lock()
        self._dirty = threading.Event()
        self._running = True
        self.stdscr = None
        self.refresh_interval = 0.5
        self._sync_check_interval = 2.0
        self._last_sync_check = 0.0
        self._sync_gap = 0
        self._menu_hint_shown = False
        if getattr(self.config.ui, "last_channel", None):
            self.active_channel = self.config.ui.last_channel

    def _resolve_direct_target(self, target: str) -> str:
        raw = str(target or "").strip()
        if (
            not raw.startswith("@")
            and self.engine
            and getattr(self.engine, "transport", None)
        ):
            resolver = getattr(self.engine.transport, "resolve_direct_target", None)
            if callable(resolver):
                try:
                    return str(resolver(raw) or raw)
                except Exception:
                    return raw
        return raw

    def _normalize_ts(self, raw_ts: Any) -> float:
        try:
            ts = float(raw_ts)
        except (TypeError, ValueError):
            return time.time()
        if ts <= 0:
            return time.time()
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        return ts

    def init_colors(self):
        if getattr(self.config.ui, "high_contrast", False):
            try:
                curses.use_default_colors()
            except curses.error:
                pass
            curses.start_color()
            try:
                curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
                curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLACK)
                curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
                curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)
                curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLACK)
                curses.init_pair(6, curses.COLOR_YELLOW, curses.COLOR_BLACK)
                curses.init_pair(7, curses.COLOR_RED, curses.COLOR_BLACK)
                curses.init_pair(8, curses.COLOR_CYAN, curses.COLOR_BLACK)
                curses.init_pair(9, curses.COLOR_CYAN, curses.COLOR_BLACK)
                curses.init_pair(10, curses.COLOR_WHITE, curses.COLOR_BLACK)
                curses.init_pair(11, curses.COLOR_WHITE, curses.COLOR_BLACK)
                curses.init_pair(12, curses.COLOR_WHITE, curses.COLOR_BLACK)
                curses.init_pair(13, curses.COLOR_YELLOW, curses.COLOR_BLACK)
                curses.init_pair(14, curses.COLOR_WHITE, curses.COLOR_BLACK)
                curses.init_pair(15, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
            except curses.error:
                pass
            return

        use_default = False
        try:
            curses.use_default_colors()
            use_default = True
        except curses.error:
            pass

        bg = -1 if use_default else curses.COLOR_BLACK

        def safe_init(pair, fg, bg_val):
            try:
                curses.init_pair(pair, fg, bg_val)
            except curses.error:
                pass

        safe_init(1, curses.COLOR_WHITE, bg)
        safe_init(2, curses.COLOR_GREEN, bg)
        safe_init(3, curses.COLOR_CYAN, bg)
        safe_init(4, curses.COLOR_WHITE, bg)
        safe_init(5, curses.COLOR_GREEN, bg)
        safe_init(6, curses.COLOR_YELLOW, bg)
        safe_init(7, curses.COLOR_RED, bg)
        safe_init(8, curses.COLOR_MAGENTA, bg)
        safe_init(9, curses.COLOR_CYAN, bg)
        safe_init(10, curses.COLOR_WHITE, bg)
        safe_init(11, curses.COLOR_GREEN, bg)
        safe_init(12, curses.COLOR_CYAN, bg)
        safe_init(13, curses.COLOR_YELLOW, bg)
        safe_init(14, curses.COLOR_GREEN, bg)
        safe_init(15, curses.COLOR_MAGENTA, bg)

    def push_message(self, msg: dict):
        logging.debug(f"push_message: {msg}")
        with self._lock:
            existing = None
            if msg.get("id"):
                for m in self.messages:
                    if m.get("id") == msg["id"]:
                        existing = m
                        break
            if existing:
                existing.update(msg)
                self.messages.sort(key=self._message_sort_key)
            else:
                self.messages.append(msg)
                self.messages.sort(key=self._message_sort_key)
                if len(self.messages) > 2000:
                    self.messages.pop(0)
        self._dirty.set()

    def force_redraw(self):
        self._dirty.set()

    def _message_sort_key(self, msg: dict):
        seq = msg.get("_seq")
        ts = self._normalize_ts(msg.get("ts", 0))
        if isinstance(seq, int):
            return (ts, seq)
        return (ts, 0)

    def update_peers(self, peers: list):
        with self._lock:
            self.peers = [
                PeerInfo(
                    id=p.get("id"), name=p.get("name"), online=p.get("online", False)
                )
                for p in peers
            ]
        self._dirty.set()

    def set_message_state(self, msg_id: str, state: str):
        with self._lock:
            for msg in reversed(self.messages):
                if msg.get("id") == msg_id:
                    msg["state"] = state
                    break
        self._dirty.set()

    def _system(self, body: str):
        self.push_message({"type": "system", "body": str(body), "ts": time.time()})

    def _show_help(self):
        self._system("Quick commands:")
        self._system("  /join @chan (or /j) | /connect <peer> (or /dm)")
        self._system(
            "  /channels | /create @chan [password] | /rename @old @new | /delete @chan"
        )
        self._system("  /port show|set <port>|random | /listen <port>")
        self._system(
            "  /peers | /ping <peer|ip:port> | /resolve <host> | /scan <host> | /lan"
        )
        self._system("  /status | /load [n|on|off] | /clear (or /cls) | /quit (or /q)")
        self._system("  /me <action> | /theme (toggle high contrast)")
        self._system("  /outbox (show queued sends)")

    def handle_command(self, text: str):
        """Handle user commands with proper error handling."""
        try:
            parts = text.strip().split()
            if not parts:
                return
            cmd = parts[0].lower().strip()
            aliases = {
                "/h": "/help",
                "/?": "/help",
                "/j": "/join",
                "/dm": "/connect",
                "/c": "/connect",
                "/p": "/ping",
                "/cls": "/clear",
                "/q": "/quit",
            }
            cmd = aliases.get(cmd, cmd)

            if cmd == "/help":
                self._show_help()
                return
            if cmd == "/clear":
                with self._lock:
                    self.messages = []
                self._dirty.set()
                return

            if cmd == "/quit":
                self._running = False
                return

            if cmd == "/theme":
                self.config.ui.high_contrast = not getattr(
                    self.config.ui, "high_contrast", False
                )
                try:
                    self.config_mgr.save(self.config)
                except Exception:
                    pass
                self.init_colors()
                self._system(
                    "High contrast enabled."
                    if self.config.ui.high_contrast
                    else "High contrast disabled."
                )
                return

            if cmd == "/me" and len(parts) > 1:
                action = " ".join(parts[1:]).strip()
                if not action:
                    self._system("Usage: /me <action>")
                    return
                msg_id = str(uuid.uuid4())
                msg = {
                    "id": msg_id,
                    "from_id": self.identity.peer_id,
                    "from_name": self.identity.username,
                    "body": f"* {self.identity.username} {action}",
                    "ts": time.time(),
                    "state": "sending",
                    "channel": self.active_channel
                    if self.active_channel.startswith("@")
                    else None,
                    "to": None
                    if self.active_channel.startswith("@")
                    else self.active_channel,
                    "enc": "none",
                    "_seq": 2_000_000_000,
                }
                self.push_message(msg)
                if self.engine and self.engine.loop:
                    if self.active_channel.startswith("@"):
                        asyncio.run_coroutine_threadsafe(
                            self.engine.send_channel(
                                self.active_channel, msg["body"], msg_id=msg_id
                            ),
                            self.engine.loop,
                        )
                    else:
                        asyncio.run_coroutine_threadsafe(
                            self.engine.send_direct(
                                self.active_channel, msg["body"], msg_id=msg_id
                            ),
                            self.engine.loop,
                        )
                return

            if cmd == "/load":
                if len(parts) > 1 and parts[1].lower() in {"off", "stop"}:
                    self.auto_load_enabled = False
                    self._system("Auto-load disabled.")
                    return
                if len(parts) > 1 and parts[1].lower() in {"on", "start"}:
                    self.auto_load_enabled = True
                    self._system(f"Auto-load enabled (limit={self.auto_load_limit}).")
                elif len(parts) > 1:
                    try:
                        self.auto_load_limit = max(1, min(int(parts[1]), 500))
                        self.auto_load_enabled = True
                        self._system(
                            f"Auto-load enabled (limit={self.auto_load_limit})."
                        )
                    except ValueError:
                        self._system("Usage: /load [n|on|off]")
                        return
                if self.engine and self.engine.loop:
                    asyncio.run_coroutine_threadsafe(
                        self.engine.transport.load_more(
                            self.active_channel, limit=self.auto_load_limit
                        ),
                        self.engine.loop,
                    )
                    asyncio.run_coroutine_threadsafe(
                        self.engine.transport.load_new_messages(
                            self.active_channel, limit=self.auto_load_limit
                        ),
                        self.engine.loop,
                    )
                return

            if cmd == "/join" and len(parts) > 1:
                chan = parts[1].strip()
                if chan.startswith("#"):
                    target = self._resolve_direct_target(chan)
                    self.active_channel = target
                    if getattr(self.config.ui, "last_channel", None) != target:
                        self.config.ui.last_channel = target
                        try:
                            self.config_mgr.save(self.config)
                        except Exception:
                            pass
                    self._system(f"Direct chat target set to {target}")
                    return
                if not chan.startswith("@"):
                    chan = "@" + chan
                if self.active_channel == chan:
                    self._system(f"Already in {chan}")
                    return
                self.active_channel = chan
                if getattr(self.config.ui, "last_channel", None) != chan:
                    self.config.ui.last_channel = chan
                    try:
                        self.config_mgr.save(self.config)
                    except Exception:
                        pass
                with self._lock:
                    self.messages = []
                if self.engine and self.engine.loop:
                    asyncio.run_coroutine_threadsafe(
                        self.engine.join_channel(chan), self.engine.loop
                    )
                    asyncio.run_coroutine_threadsafe(
                        self.engine.transport.load_new_messages(
                            self.active_channel, limit=self.auto_load_limit
                        ),
                        self.engine.loop,
                    )
                self._system(f"Switched to channel {chan}")
                return

            if cmd == "/connect" and len(parts) > 1:
                requested = parts[1].strip()
                target = self._resolve_direct_target(requested)
                self.active_channel = target
                if getattr(self.config.ui, "last_channel", None) != target:
                    self.config.ui.last_channel = target
                    try:
                        self.config_mgr.save(self.config)
                    except Exception:
                        pass
                if len(parts) > 2:
                    text_to_send = " ".join(parts[2:]).strip()
                    if not text_to_send:
                        self._system("Empty message skipped.")
                        return
                    msg_id = str(uuid.uuid4())
                    self.push_message(
                        {
                            "id": msg_id,
                            "from_id": self.identity.peer_id,
                            "from_name": self.identity.username,
                            "body": text_to_send,
                            "ts": time.time(),
                            "state": "sending",
                            "channel": None,
                            "to": target,
                            "enc": "none",
                            "_seq": 2_000_000_000,
                        }
                    )
                    if self.engine and self.engine.loop:
                        asyncio.run_coroutine_threadsafe(
                            self.engine.send_direct(
                                target, text_to_send, msg_id=msg_id
                            ),
                            self.engine.loop,
                        )
                self._system(
                    f"Direct chat target set to {target}"
                    if target == requested
                    else f"Direct chat target set to {target} (resolved from {requested})"
                )
                return

            if cmd == "/channels":
                if self.engine and self.engine.loop:
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.list_channels(),
                        self.engine.loop,
                    )
                    self._system("Loading channels...")

                    def _channels_done(f):
                        try:
                            chans = f.result() or []
                            if not chans:
                                self._system("No channels found.")
                                return
                            self._system("Channels: " + ", ".join(chans[:30]))
                        except Exception as e:
                            self._system(f"Channel list failed: {e}")

                    fut.add_done_callback(_channels_done)
                return

            if cmd == "/create" and len(parts) > 1:
                chan = parts[1].strip()
                if not chan.startswith("@"):
                    chan = "@" + chan
                if self.active_channel == chan:
                    self._system(f"Already in {chan}")
                    return
                password = parts[2].strip() if len(parts) > 2 else None
                if self.engine and self.engine.loop:
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.create_channel(chan, password=password),
                        self.engine.loop,
                    )

                    def _create_done(f):
                        try:
                            res = f.result() or {}
                            if res.get("ok"):
                                self._system(f"Channel ready: {chan}")
                            else:
                                self._system(
                                    f"Create channel failed: {res.get('error', 'unknown error')}"
                                )
                        except Exception as e:
                            self._system(f"Create channel error: {e}")

                    fut.add_done_callback(_create_done)
                return

            if cmd == "/delete" and len(parts) > 1:
                chan = parts[1].strip()
                if not chan.startswith("@"):
                    chan = "@" + chan
                if chan == "@broadcast":
                    self._system("@broadcast cannot be deleted.")
                    return
                confirm = " ".join(parts[2:]).strip().lower()
                if confirm not in {"--yes", "-y", "yes"}:
                    self._system(f"Confirm delete {chan}: /delete {chan} --yes")
                    return
                if self.engine and self.engine.loop:
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.delete_channel(chan),
                        self.engine.loop,
                    )

                    def _delete_done(f):
                        try:
                            res = f.result() or {}
                            if res.get("ok"):
                                self._system(f"Deleted channel {chan}")
                                if self.active_channel == chan:
                                    self.active_channel = "@broadcast"
                                    self._system("Switched back to @broadcast")
                            else:
                                self._system(f"Delete failed for {chan}")
                        except Exception as e:
                            self._system(f"Delete channel error: {e}")

                    fut.add_done_callback(_delete_done)
                return

            if cmd == "/rename" and len(parts) > 2:
                old_chan = parts[1].strip()
                new_chan = parts[2].strip()
                if not old_chan.startswith("@"):
                    old_chan = "@" + old_chan
                if not new_chan.startswith("@"):
                    new_chan = "@" + new_chan
                if old_chan == "@broadcast" or new_chan == "@broadcast":
                    self._system("@broadcast cannot be renamed.")
                    return
                if old_chan == new_chan:
                    self._system("Rename skipped: channel names match.")
                    return
                if self.engine and self.engine.loop:
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.rename_channel(old_chan, new_chan),
                        self.engine.loop,
                    )

                    def _rename_done(f):
                        try:
                            res = f.result() or {}
                            if res.get("ok"):
                                self._system(f"Renamed {old_chan} -> {new_chan}")
                                if self.active_channel == old_chan:
                                    self.active_channel = new_chan
                                    if (
                                        getattr(self.config.ui, "last_channel", None)
                                        == old_chan
                                    ):
                                        self.config.ui.last_channel = new_chan
                                        try:
                                            self.config_mgr.save(self.config)
                                        except Exception:
                                            pass
                                    self._system(f"Switched to {new_chan}")
                            else:
                                self._system(
                                    f"Rename failed: {res.get('error', 'invalid channel name')}"
                                )
                        except Exception as e:
                            self._system(f"Rename error: {e}")

                    fut.add_done_callback(_rename_done)
                return

            if cmd == "/status":
                s = self.engine.transport.status
                sync = self.engine.transport.get_sync_status(self.active_channel)
                self._system(
                    f"P2P Status: {s.get('ip')}:{s.get('port')} (udp:{s.get('udp_port')}) | "
                    f"last={s.get('last_transport')} | fb={self.engine.transport.fb.db_url} | "
                    f"seen={sync.get('seen_count')} remote={sync.get('remote_count')} gap={sync.get('gap')}"
                )
                return

            if cmd == "/peers":
                peers = self.engine.transport.list_online_peers()
                if not peers:
                    self._system("No peers online.")
                else:
                    for p in peers:
                        lat = p.get("latency_ms")
                        lat_tag = f" {lat}ms" if isinstance(lat, int) else ""
                        body = f"{p.get('name')} ({p.get('id')}) {p.get('ip')}:{p.get('port')} udp:{p.get('udp_port')}{lat_tag}"
                        self._system(body)
                return

            if cmd == "/ping" and len(parts) > 1:
                target = parts[1]
                if self.engine and self.engine.loop:
                    if ":" in target and not target.count("-") >= 4:
                        host, port_str = target.rsplit(":", 1)
                        try:
                            port = int(port_str)
                        except ValueError:
                            self._system("Usage: /ping <peer_id|ip[:port]>")
                            return
                        fut = asyncio.run_coroutine_threadsafe(
                            self.engine.transport.ping_host(host, port),
                            self.engine.loop,
                        )
                        self._system(f"Pinging {host}:{port}...")
                    else:
                        fut = asyncio.run_coroutine_threadsafe(
                            self.engine.transport.ping_peer(target), self.engine.loop
                        )
                        self._system(f"Pinging peer {target}...")

                    def _ping_done(f):
                        try:
                            result = f.result()
                            if result.get("ok"):
                                peer = result.get("peer_id") or result.get("host")
                                self._system(
                                    f"Ping ok: {peer} in {result.get('latency_ms')} ms"
                                )
                            else:
                                self._system(
                                    f"Ping failed: {result.get('error', 'unknown error')}"
                                )
                        except Exception as e:
                            self._system(f"Ping error: {e}")

                    fut.add_done_callback(_ping_done)
                return

            if cmd == "/resolve" and len(parts) > 1:
                host = parts[1].strip()
                if self.engine and self.engine.loop:
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.resolve_host(host), self.engine.loop
                    )
                    self._system(f"Resolving {host}...")

                    def _resolve_done(f):
                        try:
                            result = f.result()
                            if not result.get("ok"):
                                self._system(
                                    f"Resolve failed: {result.get('error', 'unknown error')}"
                                )
                                return
                            addrs = result.get("addresses") or []
                            self._system(
                                f"{host} -> {', '.join(addrs)} ({result.get('latency_ms')} ms)"
                            )
                        except Exception as e:
                            self._system(f"Resolve error: {e}")

                    fut.add_done_callback(_resolve_done)
                return

            if cmd == "/scan" and len(parts) > 1:
                host = parts[1].strip()
                if self.engine and self.engine.loop:
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.scan_common_ports(host), self.engine.loop
                    )
                    self._system(f"Scanning common ports on {host}...")

                    def _scan_done(f):
                        try:
                            result = f.result()
                            open_ports = result.get("open_ports") or []
                            self._system(
                                f"Scan done in {result.get('elapsed_ms')} ms. Open: {len(open_ports)}"
                            )
                            for item in open_ports:
                                self._system(
                                    f"{host}:{item.get('port')} ({item.get('latency_ms')} ms)"
                                )
                        except Exception as e:
                            self._system(f"Scan error: {e}")

                    fut.add_done_callback(_scan_done)
                return

            if cmd == "/lan":
                if self.engine and self.engine.loop:
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.list_lan_devices(), self.engine.loop
                    )
                    self._system("Discovering LAN devices...")

                    def _lan_done(f):
                        try:
                            result = f.result()
                            if not result.get("ok"):
                                self._system(
                                    f"LAN discovery failed: {result.get('error', 'unknown error')}"
                                )
                                return
                            devices = result.get("devices") or []
                            self._system(f"LAN devices in ARP cache: {len(devices)}")
                            for d in devices[:30]:
                                self._system(
                                    f"{d.get('ip')} {d.get('mac')} ({d.get('host')})"
                                )
                        except Exception as e:
                            self._system(f"LAN error: {e}")

                    fut.add_done_callback(_lan_done)
                return

            if cmd == "/listen" and len(parts) > 1:
                try:
                    port = int(parts[1])
                    if port < 1 or port > 65535:
                        raise ValueError
                except ValueError:
                    self._system("Usage: /listen <1-65535>")
                    return
                if self.engine and self.engine.loop:
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.set_listen_port(port), self.engine.loop
                    )
                    self._system(f"Switching listener to port {port}...")

                    def _done(f):
                        try:
                            bound = f.result()
                            self._system(f"Listening on port {bound}.")
                        except Exception as e:
                            self._system(f"Failed to bind port {port}: {e}")

                    fut.add_done_callback(_done)
                return

            if cmd == "/port":
                if len(parts) == 1 or parts[1] == "show":
                    s = self.engine.transport.status
                    self._system(
                        f"Ports tcp:{s.get('port')} udp:{s.get('udp_port')} ip:{s.get('ip')}"
                    )
                    return
                sub = parts[1].lower()
                if sub == "set" and len(parts) > 2:
                    self.handle_command(f"/listen {parts[2]}")
                    return
                if sub == "random":
                    if self.engine and self.engine.loop:
                        fut = asyncio.run_coroutine_threadsafe(
                            self.engine.transport.create_random_listen_port(),
                            self.engine.loop,
                        )
                        self._system("Creating random listen port...")

                        def _port_done(f):
                            try:
                                bound = f.result()
                                self._system(f"Random listen port: {bound}")
                            except Exception as e:
                                self._system(f"Port error: {e}")

                        fut.add_done_callback(_port_done)
                    return
                if sub == "test" and len(parts) > 2:
                    self.handle_command(f"/ping {parts[2]}")
                    return
                self._system("Usage: /port <show|set <port>|random|test <ip:port>>")
                return

            if cmd == "/menu":
                self._system(
                    "In-session shortcuts: /help, /join, /connect, /channels, /create, /rename, /delete, /port"
                )
                return

            if cmd == "/outbox":
                pending = []
                try:
                    pending = self.engine.storage.list_outbox(limit=10)
                except Exception:
                    pending = []
                if not pending:
                    self._system("Outbox empty.")
                else:
                    self._system(f"Outbox pending: {len(pending)}")
                    for m in pending:
                        self._system(
                            f"- {m.get('to') or m.get('channel')} {m.get('body')}"
                        )
                return

            if cmd == "/direct" and len(parts) > 2:
                target = parts[1]
                text_to_send = " ".join(parts[2:])
                if ":" in target:
                    host, port_str = target.rsplit(":", 1)
                    try:
                        port = int(port_str)
                    except ValueError:
                        self._system("Usage: /direct <ip>:<port> <text>")
                        return
                    if self.engine and self.engine.loop:
                        asyncio.run_coroutine_threadsafe(
                            self.engine.transport.send_raw(host, port, text_to_send),
                            self.engine.loop,
                        )
                        self._system(f"Sent raw text to {target}")
                else:
                    self._system("Usage: /direct <ip>:<port> <text>")
                return

            self._system(f"Unknown command: {cmd}. Try /help")
        except Exception as e:
            self._system(f"Command error: {e}")

    def handle_key(self, key):
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if self.input_cursor > 0:
                self.input_buf = (
                    self.input_buf[: self.input_cursor - 1]
                    + self.input_buf[self.input_cursor :]
                )
                self.input_cursor -= 1
        elif key == curses.KEY_DC:
            if self.input_cursor < len(self.input_buf):
                self.input_buf = (
                    self.input_buf[: self.input_cursor]
                    + self.input_buf[self.input_cursor + 1 :]
                )
        elif key == curses.KEY_LEFT:
            self.input_cursor = max(0, self.input_cursor - 1)
        elif key == curses.KEY_RIGHT:
            self.input_cursor = min(len(self.input_buf), self.input_cursor + 1)
        elif key == curses.KEY_UP:
            self.scroll_offset += 1
        elif key == curses.KEY_DOWN:
            self.scroll_offset = max(0, self.scroll_offset - 1)
        elif key == 21:
            self.input_buf = ""
            self.input_cursor = 0
        elif key in (10, 13):
            raw_text = self.input_buf
            text = raw_text.strip()
            if text:
                if text.startswith("/"):
                    self.handle_command(text)
                else:
                    msg_id = str(uuid.uuid4())
                    is_channel = self.active_channel.startswith("@")
                    msg = {
                        "id": msg_id,
                        "from_id": self.identity.peer_id,
                        "from_name": self.identity.username,
                        "body": text,
                        "ts": time.time(),
                        "state": "sending",
                        "channel": self.active_channel if is_channel else None,
                        "to": None if is_channel else self.active_channel,
                        "enc": "none",
                        "_seq": 2_000_000_000,
                    }
                    self.push_message(msg)
                    if self.engine and self.engine.loop:
                        if is_channel:
                            asyncio.run_coroutine_threadsafe(
                                self.engine.send_channel(
                                    self.active_channel, text, msg_id=msg_id
                                ),
                                self.engine.loop,
                            )
                        else:
                            target = self._resolve_direct_target(self.active_channel)
                            if target != self.active_channel:
                                self.active_channel = target
                                if (
                                    getattr(self.config.ui, "last_channel", None)
                                    != target
                                ):
                                    self.config.ui.last_channel = target
                                    try:
                                        self.config_mgr.save(self.config)
                                    except Exception:
                                        pass
                                self.push_message(
                                    {
                                        "type": "system",
                                        "body": f"Direct target resolved to {target}",
                                        "ts": time.time(),
                                    }
                                )
                            asyncio.run_coroutine_threadsafe(
                                self.engine.send_direct(target, text, msg_id=msg_id),
                                self.engine.loop,
                            )
            self.input_buf = ""
            self.input_cursor = 0
        elif 32 <= key <= 126:
            char = chr(key)
            self.input_buf = (
                self.input_buf[: self.input_cursor]
                + char
                + self.input_buf[self.input_cursor :]
            )
            self.input_cursor += 1
        self._dirty.set()

    def redraw(self, stdscr):
        H, W = stdscr.getmaxyx()
        stdscr.erase()

        if H < 8 or W < 40:
            try:
                stdscr.addstr(
                    0,
                    0,
                    "═══ Hermes ═══"[: W - 1],
                    curses.color_pair(11) | curses.A_BOLD,
                )
                stdscr.addstr(
                    1,
                    0,
                    "Window too small. Resize to continue."[: W - 1],
                    curses.color_pair(10),
                )
            except curses.error:
                pass
            stdscr.refresh()
            return

        s = self.engine.transport.status
        online_count = sum(1 for p in self.peers if p.online)
        enc_mode = getattr(self.config.crypto, "default_mode", "none")

        header_left = f"╔══ Hermes v{VERSION} ══ {self.active_channel}"
        header_right = f"online:{online_count} ══╗"
        if len(header_left) + len(header_right) + 4 > W:
            trim_len = max(0, W - len(header_right) - 8)
            header_left = f"╔══ Hermes v{VERSION} ══ " + self.active_channel[:trim_len]
        header_full = f"{header_left}{' ' * max(0, W - len(header_left) - len(header_right) - 4)}{header_right}"
        try:
            stdscr.addstr(
                0, 0, header_full[: W - 1], curses.color_pair(11) | curses.A_BOLD
            )
        except curses.error:
            pass

        conn_info = (
            f"║ TCP:{s.get('port') or '-'} UDP:{s.get('udp_port') or '-'} "
            f"| {s.get('last_transport') or 'idle'} "
            f"| enc:{enc_mode}"
        )
        conn_info = conn_info.ljust(W - 2) + "║"
        try:
            stdscr.addstr(1, 0, conn_info[: W - 1], curses.color_pair(3))
        except curses.error:
            pass

        try:
            stdscr.addstr(2, 0, "╠" + "═" * (W - 2) + "╣", curses.color_pair(10))
        except curses.error:
            pass

        try:
            stdscr.addstr(H - 2, 0, "╚" + "═" * (W - 2) + "╝", curses.color_pair(10))
        except curses.error:
            pass

        prefix = "╚► "
        hint = "Enter | /help"
        try:
            stdscr.addstr(H - 1, 0, prefix, curses.color_pair(11) | curses.A_BOLD)
            stdscr.addstr(
                H - 1,
                len(prefix),
                self.input_buf[: W - len(prefix) - len(hint) - 2],
                curses.color_pair(2) | curses.A_BOLD,
            )
            if W > 50:
                stdscr.addstr(H - 1, W - len(hint) - 1, hint, curses.color_pair(10))
        except curses.error:
            pass

        msg_rows = H - 5
        if msg_rows > 0:
            with self._lock:
                display_msgs = []
                for m in self.messages:
                    if m.get("type") == "system":
                        display_msgs.append(m)
                        continue
                    target = m.get("channel") or m.get("to")
                    if (
                        target == self.active_channel
                        or m.get("from_id") == self.active_channel
                    ):
                        display_msgs.append(m)
                    elif self.active_channel == "@broadcast" and (
                        target == "*" or target == "@broadcast"
                    ):
                        display_msgs.append(m)

                curr_row = 3
                prev_date = None
                line_items: list[tuple[str, Any]] = []
                for msg in display_msgs:
                    if curr_row > H - 3:
                        break
                    msg_ts = self._normalize_ts(msg.get("ts", time.time()))
                    msg_date = datetime.fromtimestamp(msg_ts).date()
                    if msg_date != prev_date:
                        line_items.append(("date", msg_date))
                        prev_date = msg_date
                    line_items.append(("msg", msg))

                end_idx = max(0, len(line_items) - self.scroll_offset)
                start_idx = max(0, end_idx - msg_rows)
                visible = line_items[start_idx:end_idx] if end_idx > start_idx else []

                for kind, payload in visible:
                    if curr_row > H - 3:
                        break
                    if kind == "date":
                        label = payload.strftime("  %A, %d %b  ")
                        if len(label) < W:
                            stdscr.addstr(
                                curr_row,
                                (W - len(label)) // 2,
                                label,
                                curses.color_pair(12),
                            )
                        curr_row += 1
                        continue

                    if curr_row > H - 3:
                        break
                    msg = payload
                    msg_ts = self._normalize_ts(msg.get("ts", time.time()))
                    stdscr.addstr(
                        curr_row,
                        0,
                        datetime.fromtimestamp(msg_ts).strftime("%H:%M "),
                        curses.color_pair(1),
                    )

                    if msg.get("type") == "system":
                        sys_text = (
                            f"  ! {msg.get('body', '')}"
                            if getattr(self.config.ui, "high_contrast", False)
                            else f"  ⚡ {msg.get('body', '')}"
                        )
                        try:
                            stdscr.addstr(
                                curr_row,
                                2,
                                sys_text[: W - 4],
                                curses.color_pair(15) | curses.A_DIM,
                            )
                        except curses.error:
                            pass
                    else:
                        is_me = msg.get("from_id") == self.identity.peer_id
                        color = curses.color_pair(5) if is_me else curses.color_pair(4)
                        sender = str(msg.get("from_name", "unknown"))[:10]
                        prefix_char = (
                            ">"
                            if getattr(self.config.ui, "high_contrast", False)
                            else ("▸" if is_me else "▹")
                        )
                        state = msg.get("state", "sent")

                        state_icon = ""
                        state_attr = curses.color_pair(2)
                        if state == "sending":
                            state_icon = (
                                " ..."
                                if getattr(self.config.ui, "high_contrast", False)
                                else " ⏳"
                            )
                            state_attr = curses.color_pair(6)
                        elif state == "sent":
                            state_icon = (
                                " ok"
                                if getattr(self.config.ui, "high_contrast", False)
                                else " ✓"
                            )
                            state_attr = curses.color_pair(14)
                        elif state == "failed":
                            state_icon = (
                                " !!"
                                if getattr(self.config.ui, "high_contrast", False)
                                else " ✗"
                            )
                            state_attr = curses.color_pair(7)

                        try:
                            stdscr.addstr(
                                curr_row,
                                2,
                                f"{prefix_char} {sender}",
                                color | curses.A_BOLD,
                            )
                            body_start = 2 + len(f"{prefix_char} {sender}")
                            body_text = str(msg.get("body", ""))
                            if body_start + len(body_text) < W - 5:
                                stdscr.addstr(
                                    curr_row,
                                    body_start + 1,
                                    body_text[: W - body_start - 5],
                                    state_attr,
                                )
                            else:
                                stdscr.addstr(
                                    curr_row,
                                    body_start + 1,
                                    body_text[: W - body_start - 5],
                                    state_attr,
                                )
                            if (
                                W - body_start - len(body_text[: W - body_start - 5])
                                > 4
                            ):
                                end_pos = min(
                                    W - 2,
                                    body_start
                                    + 1
                                    + len(body_text[: W - body_start - 5]),
                                )
                                stdscr.addstr(
                                    curr_row,
                                    end_pos,
                                    state_icon,
                                    state_attr | curses.A_DIM,
                                )
                        except curses.error:
                            pass

                        if msg.get("enc") and msg["enc"] != "none":
                            enc_tag = f" [{msg['enc'].replace('custom:', '')[:4]}]"
                            enc_pos = W - len(enc_tag) - 1
                            if enc_pos > 20:
                                try:
                                    stdscr.addstr(
                                        curr_row,
                                        enc_pos,
                                        enc_tag,
                                        curses.color_pair(8) | curses.A_DIM,
                                    )
                                except curses.error:
                                    pass
                    curr_row += 1

        cursor_pos = min(W - 2, len(prefix) + self.input_cursor)
        try:
            stdscr.move(H - 1, cursor_pos)
        except curses.error:
            pass
        stdscr.refresh()

    def _main(self, stdscr):
        self.stdscr = stdscr
        self.init_colors()
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        stdscr.nodelay(True)
        stdscr.keypad(True)
        last_redraw = 0
        redraw_interval = self.refresh_interval
        while self._running:
            now = time.time()
            should_redraw = self._dirty.is_set() or (
                now - last_redraw >= redraw_interval
            )

            if should_redraw:
                if self._dirty.is_set():
                    self._dirty.clear()
                try:
                    self.redraw(stdscr)
                except curses.error:
                    pass
                last_redraw = now

            key = stdscr.getch()
            if key == curses.ERR:
                time.sleep(0.02)
                continue
            if key == curses.KEY_RESIZE:
                stdscr.erase()
            else:
                self.handle_key(key)
            last_redraw = time.time()

    def _auto_loader_loop(self):
        last_presence_check = 0
        presence_interval = 10.0
        last_load_check = 0.0
        while self._running:
            try:
                now = time.time()
                if (
                    self.auto_load_enabled
                    and self.engine
                    and self.engine.loop
                    and self.active_channel
                    and (now - last_load_check) >= self.refresh_interval
                ):
                    last_load_check = now
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self.engine.transport.load_new_messages(
                                self.active_channel, limit=self.auto_load_limit
                            ),
                            self.engine.loop,
                        )
                    except Exception:
                        pass

                if now - self._last_sync_check >= self._sync_check_interval:
                    self._last_sync_check = now
                    try:
                        sync = self.engine.transport.get_sync_status(
                            self.active_channel
                        )
                        gap = int(sync.get("gap") or 0)
                        self._sync_gap = gap
                        if gap > 0 and self.engine and self.engine.loop:
                            asyncio.run_coroutine_threadsafe(
                                self.engine.transport.load_new_messages(
                                    self.active_channel,
                                    limit=max(self.auto_load_limit, min(gap * 2, 500)),
                                ),
                                self.engine.loop,
                            )
                    except Exception:
                        pass

                if now - last_presence_check >= presence_interval:
                    last_presence_check = now
                    if self.engine and self.engine.loop:
                        try:
                            asyncio.run_coroutine_threadsafe(
                                self.engine.transport.update_presence(),
                                self.engine.loop,
                            )
                        except Exception:
                            pass
                        peers = self.engine.transport.list_online_peers()
                        self.update_peers(peers)
            except Exception:
                pass
            time.sleep(0.1)

    def run(self):
        self._loader_thread = threading.Thread(
            target=self._auto_loader_loop, daemon=True
        )
        self._loader_thread.start()
        self._running = True
        try:
            curses.wrapper(self._main)
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            self.stdscr = None
            try:
                curses.endwin()
            except curses.error:
                pass

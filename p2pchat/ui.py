import curses
import threading
import time
import uuid
import asyncio
from datetime import datetime
from dataclasses import dataclass
from typing import Any
import logging

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
        use_default = False
        try:
            curses.use_default_colors()
            use_default = True
        except:
            pass

        bg = -1 if use_default else curses.COLOR_BLACK
        
        def safe_init(pair, fg, bg):
            try:
                curses.init_pair(pair, fg, bg)
            except:
                pass

        safe_init(1, curses.COLOR_WHITE, bg)    # base text
        safe_init(2, curses.COLOR_WHITE, bg)    # input text
        safe_init(3, curses.COLOR_CYAN, bg)     # accent
        safe_init(4, curses.COLOR_WHITE, bg)    # peer sender
        safe_init(5, curses.COLOR_CYAN, bg)     # own sender
        safe_init(6, curses.COLOR_YELLOW, bg)   # sending
        safe_init(7, curses.COLOR_RED, bg)      # failed
        safe_init(8, curses.COLOR_BLUE, bg)     # enc tag
        safe_init(9, curses.COLOR_CYAN, bg)     # header
        safe_init(10, curses.COLOR_WHITE, bg)   # muted
        safe_init(11, curses.COLOR_WHITE, bg)   # title
        safe_init(12, curses.COLOR_CYAN, bg)    # date separator

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

    def handle_command(self, text: str):
        parts = text.split()
        cmd = parts[0].lower()

        if cmd == "/help":
            self.push_message(
                {
                    "type": "system",
                    "body": (
                        "Commands: /join <@chan>, /connect <peer_id>, /load [n|on|off], "
                        "/listen <port>, /port <show|set|random|test>, /peers, /ping <peer_id|ip[:port]>, "
                        "/resolve <host>, /scan <host>, /lan, "
                        "/status, /clear, /quit"
                    ),
                    "ts": time.time(),
                }
            )
        elif cmd == "/clear":
            with self._lock:
                self.messages = []
            self._dirty.set()
        elif cmd == "/quit":
            self._running = False
        elif cmd == "/load":
            if len(parts) > 1 and parts[1].lower() in {"off", "stop"}:
                self.auto_load_enabled = False
                self.push_message(
                    {"type": "system", "body": "Auto-load disabled.", "ts": time.time()}
                )
                return
            if len(parts) > 1 and parts[1].lower() in {"on", "start"}:
                self.auto_load_enabled = True
                self.push_message(
                    {
                        "type": "system",
                        "body": f"Auto-load enabled (limit={self.auto_load_limit}).",
                        "ts": time.time(),
                    }
                )
            elif len(parts) > 1:
                try:
                    self.auto_load_limit = max(1, min(int(parts[1]), 500))
                    self.auto_load_enabled = True
                    self.push_message(
                        {
                            "type": "system",
                            "body": f"Auto-load enabled (limit={self.auto_load_limit}).",
                            "ts": time.time(),
                        }
                    )
                except ValueError:
                    self.push_message(
                        {
                            "type": "system",
                            "body": "Usage: /load [n|on|off]",
                            "ts": time.time(),
                        }
                    )
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
        elif cmd == "/join" and len(parts) > 1:
            chan = parts[1]
            if not chan.startswith("@"):
                chan = "@" + chan
            self.active_channel = chan
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
        elif cmd == "/connect" and len(parts) > 1:
            target = parts[1].strip()
            self.active_channel = target
            self.push_message(
                {
                    "type": "system",
                    "body": f"Direct chat target set to {target}",
                    "ts": time.time(),
                }
            )
        elif cmd == "/status":
            s = self.engine.transport.status
            sync = self.engine.transport.get_sync_status(self.active_channel)
            self.push_message(
                {
                    "type": "system",
                    "body": (
                        f"P2P Status: {s.get('ip')}:{s.get('port')} "
                        f"(udp:{s.get('udp_port')}) "
                        f"| last={s.get('last_transport')} "
                        f"| fb={self.engine.transport.fb.db_url} "
                        f"| seen={sync.get('seen_count')} remote={sync.get('remote_count')} gap={sync.get('gap')}"
                    ),
                    "ts": time.time(),
                }
            )
        elif cmd == "/peers":
            peers = self.engine.transport.list_online_peers()
            if not peers:
                self.push_message(
                    {"type": "system", "body": "No peers online.", "ts": time.time()}
                )
            else:
                for p in peers:
                    body = f"{p.get('name')} ({p.get('id')}) {p.get('ip')}:{p.get('port')} udp:{p.get('udp_port')}"
                    self.push_message(
                        {"type": "system", "body": body, "ts": time.time()}
                    )
        elif cmd == "/ping" and len(parts) > 1:
            target = parts[1]
            if self.engine and self.engine.loop:
                if ":" in target and not target.count("-") >= 4:
                    host, port_str = target.rsplit(":", 1)
                    try:
                        port = int(port_str)
                    except ValueError:
                        self.push_message(
                            {
                                "type": "system",
                                "body": "Usage: /ping <peer_id|ip[:port]>",
                                "ts": time.time(),
                            }
                        )
                        return
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.ping_host(host, port), self.engine.loop
                    )
                    self.push_message(
                        {
                            "type": "system",
                            "body": f"Pinging {host}:{port}...",
                            "ts": time.time(),
                        }
                    )
                else:
                    fut = asyncio.run_coroutine_threadsafe(
                        self.engine.transport.ping_peer(target), self.engine.loop
                    )
                    self.push_message(
                        {
                            "type": "system",
                            "body": f"Pinging peer {target}...",
                            "ts": time.time(),
                        }
                    )

                def _ping_done(f):
                    try:
                        result = f.result()
                        if result.get("ok"):
                            peer = result.get("peer_id") or result.get("host")
                            self.push_message(
                                {
                                    "type": "system",
                                    "body": f"Ping ok: {peer} in {result.get('latency_ms')} ms",
                                    "ts": time.time(),
                                }
                            )
                        else:
                            self.push_message(
                                {
                                    "type": "system",
                                    "body": f"Ping failed: {result.get('error', 'unknown error')}",
                                    "ts": time.time(),
                                }
                            )
                    except Exception as e:
                        self.push_message(
                            {
                                "type": "system",
                                "body": f"Ping error: {e}",
                                "ts": time.time(),
                            }
                        )

                fut.add_done_callback(_ping_done)
        elif cmd == "/resolve" and len(parts) > 1:
            host = parts[1].strip()
            if self.engine and self.engine.loop:
                fut = asyncio.run_coroutine_threadsafe(
                    self.engine.transport.resolve_host(host), self.engine.loop
                )
                self.push_message(
                    {
                        "type": "system",
                        "body": f"Resolving {host}...",
                        "ts": time.time(),
                    }
                )

                def _resolve_done(f):
                    try:
                        result = f.result()
                        if not result.get("ok"):
                            self.push_message(
                                {
                                    "type": "system",
                                    "body": f"Resolve failed: {result.get('error', 'unknown error')}",
                                    "ts": time.time(),
                                }
                            )
                            return
                        addrs = result.get("addresses") or []
                        self.push_message(
                            {
                                "type": "system",
                                "body": f"{host} -> {', '.join(addrs)} ({result.get('latency_ms')} ms)",
                                "ts": time.time(),
                            }
                        )
                    except Exception as e:
                        self.push_message(
                            {
                                "type": "system",
                                "body": f"Resolve error: {e}",
                                "ts": time.time(),
                            }
                        )

                fut.add_done_callback(_resolve_done)
        elif cmd == "/scan" and len(parts) > 1:
            host = parts[1].strip()
            if self.engine and self.engine.loop:
                fut = asyncio.run_coroutine_threadsafe(
                    self.engine.transport.scan_common_ports(host), self.engine.loop
                )
                self.push_message(
                    {
                        "type": "system",
                        "body": f"Scanning common ports on {host}...",
                        "ts": time.time(),
                    }
                )

                def _scan_done(f):
                    try:
                        result = f.result()
                        open_ports = result.get("open_ports") or []
                        self.push_message(
                            {
                                "type": "system",
                                "body": f"Scan done in {result.get('elapsed_ms')} ms. Open: {len(open_ports)}",
                                "ts": time.time(),
                            }
                        )
                        for item in open_ports:
                            self.push_message(
                                {
                                    "type": "system",
                                    "body": f"{host}:{item.get('port')} ({item.get('latency_ms')} ms)",
                                    "ts": time.time(),
                                }
                            )
                    except Exception as e:
                        self.push_message(
                            {
                                "type": "system",
                                "body": f"Scan error: {e}",
                                "ts": time.time(),
                            }
                        )

                fut.add_done_callback(_scan_done)
        elif cmd == "/lan":
            if self.engine and self.engine.loop:
                fut = asyncio.run_coroutine_threadsafe(
                    self.engine.transport.list_lan_devices(), self.engine.loop
                )
                self.push_message(
                    {
                        "type": "system",
                        "body": "Discovering LAN devices...",
                        "ts": time.time(),
                    }
                )

                def _lan_done(f):
                    try:
                        result = f.result()
                        if not result.get("ok"):
                            self.push_message(
                                {
                                    "type": "system",
                                    "body": f"LAN discovery failed: {result.get('error', 'unknown error')}",
                                    "ts": time.time(),
                                }
                            )
                            return
                        devices = result.get("devices") or []
                        self.push_message(
                            {
                                "type": "system",
                                "body": f"LAN devices in ARP cache: {len(devices)}",
                                "ts": time.time(),
                            }
                        )
                        for d in devices[:30]:
                            self.push_message(
                                {
                                    "type": "system",
                                    "body": f"{d.get('ip')} {d.get('mac')} ({d.get('host')})",
                                    "ts": time.time(),
                                }
                            )
                    except Exception as e:
                        self.push_message(
                            {
                                "type": "system",
                                "body": f"LAN error: {e}",
                                "ts": time.time(),
                            }
                        )

                fut.add_done_callback(_lan_done)
        elif cmd == "/listen" and len(parts) > 1:
            try:
                port = int(parts[1])
                if port < 1 or port > 65535:
                    raise ValueError
            except ValueError:
                self.push_message(
                    {
                        "type": "system",
                        "body": "Usage: /listen <1-65535>",
                        "ts": time.time(),
                    }
                )
                return
            if self.engine and self.engine.loop:
                fut = asyncio.run_coroutine_threadsafe(
                    self.engine.transport.set_listen_port(port), self.engine.loop
                )
                self.push_message(
                    {
                        "type": "system",
                        "body": f"Switching listener to port {port}...",
                        "ts": time.time(),
                    }
                )

                def _done(f):
                    try:
                        bound = f.result()
                        self.push_message(
                            {
                                "type": "system",
                                "body": f"Listening on port {bound}.",
                                "ts": time.time(),
                            }
                        )
                    except Exception as e:
                        self.push_message(
                            {
                                "type": "system",
                                "body": f"Failed to bind port {port}: {e}",
                                "ts": time.time(),
                            }
                        )

                fut.add_done_callback(_done)
        elif cmd == "/port":
            if len(parts) == 1 or parts[1] == "show":
                s = self.engine.transport.status
                self.push_message(
                    {
                        "type": "system",
                        "body": f"Ports tcp:{s.get('port')} udp:{s.get('udp_port')} ip:{s.get('ip')}",
                        "ts": time.time(),
                    }
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
                    self.push_message(
                        {
                            "type": "system",
                            "body": "Creating random listen port...",
                            "ts": time.time(),
                        }
                    )

                    def _port_done(f):
                        try:
                            bound = f.result()
                            self.push_message(
                                {
                                    "type": "system",
                                    "body": f"Random listen port: {bound}",
                                    "ts": time.time(),
                                }
                            )
                        except Exception as e:
                            self.push_message(
                                {
                                    "type": "system",
                                    "body": f"Port error: {e}",
                                    "ts": time.time(),
                                }
                            )

                    fut.add_done_callback(_port_done)
                return
            if sub == "test" and len(parts) > 2:
                self.handle_command(f"/ping {parts[2]}")
                return
            self.push_message(
                {
                    "type": "system",
                    "body": "Usage: /port <show|set <port>|random|test <ip:port>>",
                    "ts": time.time(),
                }
            )
        elif cmd == "/menu":
            self.push_message(
                {
                    "type": "system",
                    "body": "Startup menu includes network tools. In-session tools: /peers, /ping, /resolve, /scan, /lan, /port.",
                    "ts": time.time(),
                }
            )
        elif cmd == "/direct" and len(parts) > 2:
            target = parts[1]
            text_to_send = " ".join(parts[2:])
            if ":" in target:
                host, port_str = target.rsplit(":", 1)
                port = int(port_str)
                if self.engine and self.engine.loop:
                    asyncio.run_coroutine_threadsafe(
                        self.engine.transport.send_raw(host, port, text_to_send),
                        self.engine.loop,
                    )
                    self.push_message(
                        {
                            "type": "system",
                            "body": f"Sent raw text to {target}",
                            "ts": time.time(),
                        }
                    )
            else:
                self.push_message(
                    {
                        "type": "system",
                        "body": "Usage: /direct <ip>:<port> <text>",
                        "ts": time.time(),
                    }
                )
        else:
            self.push_message(
                {"type": "system", "body": f"Unknown command: {cmd}", "ts": time.time()}
            )

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
            text = self.input_buf.strip()
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
                            asyncio.run_coroutine_threadsafe(
                                self.engine.send_direct(
                                    self.active_channel, text, msg_id=msg_id
                                ),
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
            stdscr.addstr(0, 0, "Hermes", curses.color_pair(11) | curses.A_BOLD)
            stdscr.addstr(1, 0, "Window too small. Resize to continue.", curses.color_pair(1))
            stdscr.refresh()
            return

        header_left = f"Hermes v{VERSION} | {self.active_channel}"
        stdscr.addstr(0, 0, header_left[: W - 1], curses.color_pair(11) | curses.A_BOLD)

        s = self.engine.transport.status
        online_count = sum(1 for p in self.peers if p.online)
        right_status = f"online:{online_count}"
        if self._sync_gap > 0:
            right_status += f" sync:+{self._sync_gap}"
        if W - len(right_status) - 1 > len(header_left) + 1:
            stdscr.addstr(0, W - len(right_status) - 1, right_status, curses.color_pair(3))

        enc_mode = getattr(self.config.crypto, 'default_mode', 'none')
        meta = (
            f"net:{s.get('last_transport') or '-'} "
            f"tcp:{s.get('port') or '-'} "
            f"udp:{s.get('udp_port') or '-'} "
            f"enc:{enc_mode}"
        )
        stdscr.addstr(1, 0, meta[: W - 1], curses.color_pair(10))

        line = "-" * (W - 1)
        stdscr.addstr(2, 0, line, curses.color_pair(10))
        stdscr.addstr(H - 2, 0, line, curses.color_pair(10))

        prefix = "> "
        stdscr.addstr(H - 1, 0, prefix[: W - 1], curses.color_pair(1))
        stdscr.addstr(
            H - 1,
            len(prefix),
            self.input_buf[: W - len(prefix) - 10],
            curses.color_pair(2),
        )
        if W > 40:
            stdscr.addstr(H - 1, W - 14, "Enter | /help", curses.color_pair(10))

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

                end_idx = len(line_items) - self.scroll_offset
                start_idx = max(0, end_idx - msg_rows)
                visible = line_items[start_idx:end_idx] if end_idx > 0 else []

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
                        stdscr.addstr(curr_row, 6, "system".ljust(12), curses.color_pair(10))
                        stdscr.addstr(
                            curr_row,
                            19,
                            str(msg.get("body", ""))[: W - 26],
                            curses.color_pair(10),
                        )
                    else:
                        is_me = msg.get("from_id") == self.identity.peer_id
                        color = curses.color_pair(5) if is_me else curses.color_pair(4)
                        sender = str(msg.get("from_name", "unknown"))[:11]
                        sender = f"{sender}{'*' if is_me else ''}"
                        stdscr.addstr(
                            curr_row,
                            6,
                            sender.ljust(12),
                            color,
                        )

                        state = msg.get("state", "sent")
                        attr = curses.color_pair(2)
                        suffix = ""
                        if state == "sending":
                            attr = curses.color_pair(6)
                            suffix = " ..."
                        elif state == "failed":
                            attr = curses.color_pair(7) | curses.A_DIM
                            suffix = " x"

                        stdscr.addstr(
                            curr_row,
                            19,
                            (str(msg.get("body", "")) + suffix)[: W - 26],
                            attr,
                        )
                        if state == "sent" and msg.get("enc") and msg["enc"] != "none":
                            tag = msg["enc"].replace("custom:", "")[:6]
                            if W - len(tag) - 1 > 16:
                                stdscr.addstr(
                                    curr_row,
                                    W - len(tag) - 1,
                                    tag,
                                    curses.color_pair(8),
                                )
                    curr_row += 1

        stdscr.move(H - 1, min(W - 1, len(prefix) + self.input_cursor))
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
                        sync = self.engine.transport.get_sync_status(self.active_channel)
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

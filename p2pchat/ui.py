import curses
import threading
import time
import uuid
import asyncio
from datetime import datetime
from dataclasses import dataclass
from typing import Any

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
        try:
            curses.use_default_colors()
        except: pass

        curses.init_pair(1, curses.COLOR_WHITE, -1)
        curses.init_pair(2, curses.COLOR_WHITE, -1)
        if curses.COLORS >= 16: curses.init_pair(2, 15, -1)

        curses.init_pair(3, curses.COLOR_GREEN, -1)
        curses.init_pair(4, curses.COLOR_WHITE, -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)
        curses.init_pair(6, curses.COLOR_WHITE, -1)
        curses.init_pair(7, curses.COLOR_WHITE, -1)
        curses.init_pair(8, curses.COLOR_WHITE, -1)
        curses.init_pair(9, curses.COLOR_GREEN, -1)
        curses.init_pair(10, curses.COLOR_WHITE, -1)
        curses.init_pair(11, curses.COLOR_WHITE, -1)
        curses.init_pair(12, curses.COLOR_CYAN, -1)

    def push_message(self, msg: dict):
        with self._lock:
            existing = None
            if msg.get("id"):
                for m in self.messages:
                    if m.get("id") == msg["id"]:
                        existing = m
                        break
            
            if existing:
                existing.update(msg)
            else:
                self.messages.append(msg)
                if len(self.messages) > 2000: self.messages.pop(0)
        self._dirty.set()

    def update_peers(self, peers: list):
        with self._lock:
            self.peers = [PeerInfo(id=p.get('id'), name=p.get('name'), online=p.get('online', False)) for p in peers]
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
            self.push_message({
                "type": "system",
                "body": "Commands: /join <@chan>, /connect <peer_id>, /load [n|on|off], /listen <port>, /clear, /quit",
                "ts": time.time()
            })
        elif cmd == "/clear":
            with self._lock: self.messages = []
            self._dirty.set()
        elif cmd == "/quit":
            self._running = False
        elif cmd == "/load":
            if len(parts) > 1 and parts[1].lower() in {"off", "stop"}:
                self.auto_load_enabled = False
                self.push_message({"type": "system", "body": "Auto-load disabled.", "ts": time.time()})
                return
            if len(parts) > 1 and parts[1].lower() in {"on", "start"}:
                self.auto_load_enabled = True
                self.push_message({"type": "system", "body": f"Auto-load enabled (limit={self.auto_load_limit}).", "ts": time.time()})
            elif len(parts) > 1:
                try:
                    self.auto_load_limit = max(1, min(int(parts[1]), 500))
                    self.auto_load_enabled = True
                    self.push_message({"type": "system", "body": f"Auto-load enabled (limit={self.auto_load_limit}).", "ts": time.time()})
                except ValueError:
                    self.push_message({"type": "system", "body": "Usage: /load [n|on|off]", "ts": time.time()})
                    return
            if self.engine and self.engine.loop:
                asyncio.run_coroutine_threadsafe(
                    self.engine.transport.load_new_messages(self.active_channel, limit=self.auto_load_limit),
                    self.engine.loop
                )
        elif cmd == "/join" and len(parts) > 1:
            chan = parts[1]
            if not chan.startswith("@"): chan = "@" + chan
            self.active_channel = chan
            with self._lock: self.messages = []
            if self.engine and self.engine.loop:
                asyncio.run_coroutine_threadsafe(self.engine.join_channel(chan), self.engine.loop)
                asyncio.run_coroutine_threadsafe(
                    self.engine.transport.load_new_messages(self.active_channel, limit=self.auto_load_limit),
                    self.engine.loop
                )
        elif cmd == "/connect" and len(parts) > 1:
            target = parts[1].strip()
            self.active_channel = target
            self.push_message({"type": "system", "body": f"Direct chat target set to {target}", "ts": time.time()})
        elif cmd == "/status":
            s = self.engine.transport.status
            self.push_message({
                "type": "system",
                "body": (
                    f"P2P Status: {s.get('ip')}:{s.get('port')} "
                    f"(udp:{s.get('udp_port')}) "
                    f"| last={s.get('last_transport')} "
                    f"| fb={self.engine.transport.fb.db_url}"
                ),
                "ts": time.time()
            })
        elif cmd == "/listen" and len(parts) > 1:
            try:
                port = int(parts[1])
                if port < 1 or port > 65535:
                    raise ValueError
            except ValueError:
                self.push_message({"type": "system", "body": "Usage: /listen <1-65535>", "ts": time.time()})
                return
            if self.engine and self.engine.loop:
                fut = asyncio.run_coroutine_threadsafe(self.engine.transport.set_listen_port(port), self.engine.loop)
                self.push_message({"type": "system", "body": f"Switching listener to port {port}...", "ts": time.time()})

                def _done(f):
                    try:
                        bound = f.result()
                        self.push_message({"type": "system", "body": f"Listening on port {bound}.", "ts": time.time()})
                    except Exception as e:
                        self.push_message({"type": "system", "body": f"Failed to bind port {port}: {e}", "ts": time.time()})

                fut.add_done_callback(_done)
        elif cmd == "/direct" and len(parts) > 2:
            target = parts[1]
            text_to_send = " ".join(parts[2:])
            if ":" in target:
                host, port_str = target.rsplit(":", 1)
                port = int(port_str)
                if self.engine and self.engine.loop:
                    asyncio.run_coroutine_threadsafe(self.engine.transport.send_raw(host, port, text_to_send), self.engine.loop)
                    self.push_message({"type": "system", "body": f"Sent raw text to {target}", "ts": time.time()})
            else:
                self.push_message({"type": "system", "body": "Usage: /direct <ip>:<port> <text>", "ts": time.time()})
        else:
            self.push_message({"type": "system", "body": f"Unknown command: {cmd}", "ts": time.time()})

    def handle_key(self, key):
        if key in (curses.KEY_BACKSPACE, 127, 8):
            if self.input_cursor > 0:
                self.input_buf = self.input_buf[:self.input_cursor-1] + self.input_buf[self.input_cursor:]
                self.input_cursor -= 1
        elif key == curses.KEY_DC:
            if self.input_cursor < len(self.input_buf):
                self.input_buf = self.input_buf[:self.input_cursor] + self.input_buf[self.input_cursor+1:]
        elif key == curses.KEY_LEFT: self.input_cursor = max(0, self.input_cursor - 1)
        elif key == curses.KEY_RIGHT: self.input_cursor = min(len(self.input_buf), self.input_cursor + 1)
        elif key == curses.KEY_UP: self.scroll_offset += 1
        elif key == curses.KEY_DOWN: self.scroll_offset = max(0, self.scroll_offset - 1)
        elif key == 21:
            self.input_buf = ""; self.input_cursor = 0
        elif key in (10, 13):
            text = self.input_buf.strip()
            if text:
                if text.startswith("/"):
                    self.handle_command(text)
                else:
                    msg_id = str(uuid.uuid4())
                    is_channel = self.active_channel.startswith("@")
                    msg = {
                        "id": msg_id, "from_id": self.identity.peer_id, "from_name": self.identity.username,
                        "body": text, "ts": time.time(), "state": "sending",
                        "channel": self.active_channel if is_channel else None,
                        "to": None if is_channel else self.active_channel,
                        "enc": "none"
                    }
                    self.push_message(msg)
                    if self.engine and self.engine.loop:
                        if is_channel:
                            asyncio.run_coroutine_threadsafe(self.engine.send_channel(self.active_channel, text, msg_id=msg_id), self.engine.loop)
                        else:
                            asyncio.run_coroutine_threadsafe(self.engine.send_direct(self.active_channel, text, msg_id=msg_id), self.engine.loop)
                self.input_buf = ""; self.input_cursor = 0; self.scroll_offset = 0
        elif 32 <= key <= 126:
            char = chr(key)
            self.input_buf = self.input_buf[:self.input_cursor] + char + self.input_buf[self.input_cursor:]
            self.input_cursor += 1

    def redraw(self, stdscr):
        H, W = stdscr.getmaxyx()
        stdscr.erase()

        stdscr.addstr(0, 0, self.active_channel[:W-30], curses.color_pair(11) | curses.A_BOLD)
        s = self.engine.transport.status
        p2p_part = f"p2p {s.get('ip')}:{s.get('port')}" if s.get('port') else "p2p offline"
        mode_str = f"{p2p_part} | fb | {getattr(self.config.crypto, 'default_mode', 'none')}"
        if len(mode_str) < W - 2: stdscr.addstr(0, W - len(mode_str) - 1, mode_str, curses.color_pair(1))

        x = 0
        with self._lock:
            for peer in self.peers:
                if x + len(peer.name) + 4 >= W: break
                stdscr.addstr(1, x, "* ", curses.color_pair(9) if peer.online else curses.color_pair(10))
                stdscr.addstr(1, x + 2, peer.name + "  ", curses.color_pair(3) if peer.online else curses.color_pair(4))
                x += len(peer.name) + 4

        line = "-" * (W - 1)
        stdscr.addstr(2, 0, line, curses.color_pair(1))
        stdscr.addstr(H-2, 0, line, curses.color_pair(1))

        prefix = f"{self.active_channel} > "
        stdscr.addstr(H-1, 0, prefix[:W-1], curses.color_pair(1))
        stdscr.addstr(H-1, len(prefix), self.input_buf[:W-len(prefix)-10], curses.color_pair(2))
        if W > 40: stdscr.addstr(H-1, W - 6, "/help", curses.color_pair(1))

        msg_rows = H - 5
        if msg_rows > 0:
            with self._lock:
                display_msgs = []
                for m in self.messages:
                    if m.get("type") == "system":
                        display_msgs.append(m); continue
                    target = m.get("channel") or m.get("to")
                    if target == self.active_channel or m.get("from_id") == self.active_channel:
                        display_msgs.append(m)
                    elif self.active_channel == "@broadcast" and (target == "*" or target == "@broadcast"):
                        display_msgs.append(m)

                start_idx = max(0, len(display_msgs) - msg_rows - self.scroll_offset)
                end_idx = len(display_msgs) - self.scroll_offset
                visible = display_msgs[start_idx:end_idx] if end_idx > 0 else []
                
                curr_row = 3
                prev_date = None
                for msg in visible:
                    if curr_row > H - 3: break
                    msg_ts = self._normalize_ts(msg.get("ts", time.time()))
                    msg_date = datetime.fromtimestamp(msg_ts).date()
                    if msg_date != prev_date:
                        label = msg_date.strftime("  %A, %d %b  ")
                        if len(label) < W:
                            stdscr.addstr(curr_row, (W - len(label)) // 2, label, curses.color_pair(12))
                            curr_row += 1
                        prev_date = msg_date

                    if curr_row > H - 3: break
                    stdscr.addstr(curr_row, 0, datetime.fromtimestamp(msg_ts).strftime("%H:%M "), curses.color_pair(1))

                    if msg.get("type") == "system":
                        stdscr.addstr(curr_row, 6, "- ".ljust(10), curses.color_pair(1))
                        stdscr.addstr(curr_row, 16, str(msg.get("body", ""))[:W-26], curses.color_pair(1) | curses.A_ITALIC)
                    else:
                        is_me = msg.get("from_id") == self.identity.peer_id
                        color = curses.color_pair(5) if is_me else (curses.color_pair(3) if any(p.id == msg.get("from_id") and p.online for p in self.peers) else curses.color_pair(4))
                        stdscr.addstr(curr_row, 6, str(msg.get("from_name", "unknown"))[:10].ljust(10), color)

                        state = msg.get("state", "sent")
                        attr = curses.color_pair(2)
                        suffix = ""
                        if state == "sending":
                            attr = curses.color_pair(6); suffix = " ..."
                        elif state == "failed":
                            attr = curses.color_pair(7) | curses.A_DIM; suffix = " x"
                        
                        stdscr.addstr(curr_row, 16, (str(msg.get("body", "")) + suffix)[:W-26], attr)
                        if state == "sent" and msg.get("enc") and msg["enc"] != "none":
                            tag = msg["enc"].replace("custom:", "")[:6]
                            if W - len(tag) - 1 > 16: stdscr.addstr(curr_row, W - len(tag) - 1, tag, curses.color_pair(8))
                    curr_row += 1

        stdscr.move(H-1, min(W-1, len(prefix) + self.input_cursor))
        stdscr.refresh()

    def _main(self, stdscr):
        self.init_colors()
        curses.curs_set(1); stdscr.nodelay(True); stdscr.keypad(True)
        while self._running:
            if self._dirty.is_set():
                self._dirty.clear()
                try: self.redraw(stdscr)
                except curses.error: pass
            key = stdscr.getch()
            if key == curses.ERR:
                time.sleep(0.02); continue
            if key == curses.KEY_RESIZE: stdscr.erase()
            else: self.handle_key(key)
            try: self.redraw(stdscr)
            except curses.error: pass

    def _auto_loader_loop(self):
        while self._running:
            if self.auto_load_enabled and self.engine and self.engine.loop and self.active_channel:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.engine.transport.load_new_messages(self.active_channel, limit=self.auto_load_limit),
                        self.engine.loop
                    )
                except Exception:
                    pass
            time.sleep(1.0)

    def run(self):
        self._loader_thread = threading.Thread(target=self._auto_loader_loop, daemon=True)
        self._loader_thread.start()
        try: curses.wrapper(self._main)
        except KeyboardInterrupt: pass
        finally: self._running = False

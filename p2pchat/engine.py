from __future__ import annotations

import logging
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .crypto import CryptoManager
from .protocol import build

logger = logging.getLogger("engine")

@dataclass
class PeerInfo:
    peer_id: str
    name: str
    connection: Any = None
    public_key: str | None = None
    last_seen: float = 0.0
    online: bool = True


@dataclass
class Engine:
    identity: Any
    config: Any
    transport: Any
    crypto: CryptoManager
    peers: dict[str, PeerInfo] = field(default_factory=dict)
    channels: dict[str, set[str]] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    active_channel: str | None = "@broadcast"
    ui: Any = None
    loop: asyncio.AbstractEventLoop | None = None

    def set_ui(self, ui: Any):
        self.ui = ui

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    async def send_direct(self, peer_id: str, text: str, msg_id: str | None = None):
        """Send a direct message to a peer."""
        mid = msg_id or str(time.time())
        body, enc = self.crypto.encrypt(text, peer_id, self.config.crypto.default_mode)
        
        msg = build(
            type_="msg",
            body=body,
            from_id=self.identity.peer_id,
            from_name=self.identity.username,
            to=peer_id,
            channel=None,
            enc=enc,
            ts=time.time()
        )
        msg["id"] = mid
        
        ok = await self.transport.send(peer_id, msg)
        if self.ui:
            self.ui.set_message_state(mid, "sent" if ok else "failed")
        return msg

    async def send_channel(self, channel: str, text: str, msg_id: str | None = None):
        """Send a message to a specific channel/group (@group)."""
        mid = msg_id or str(time.time())
        body, enc = self.crypto.encrypt(text, channel, self.config.crypto.default_mode)
        
        msg = build(
            type_="msg",
            body=body,
            from_id=self.identity.peer_id,
            from_name=self.identity.username,
            to=channel,
            channel=channel,
            enc=enc,
            ts=time.time()
        )
        msg["id"] = mid
        
        ok = await self.transport.send(channel, msg)
        if self.ui:
            self.ui.set_message_state(mid, "sent" if ok else "failed")
        return msg

    async def broadcast(self, text: str, msg_id: str | None = None):
        """Broadcast a message to everyone."""
        mid = msg_id or str(time.time())
        body, enc = self.crypto.encrypt(text, "*", self.config.crypto.default_mode)
        
        msg = build(
            type_="broadcast",
            body=body,
            from_id=self.identity.peer_id,
            from_name=self.identity.username,
            to="*",
            channel=None,
            enc=enc,
            ts=time.time()
        )
        msg["id"] = mid
        
        ok = await self.transport.broadcast(msg)
        if self.ui:
            self.ui.set_message_state(mid, "sent" if ok else "failed")
        return msg

    def on_message(self, msg: dict):
        """Handle an incoming message from the transport."""
        if not msg: return
        msg = dict(msg)
        from_id = msg.get("from_id")
        from_name = msg.get("from_name")
        
        if from_id:
            if from_id not in self.peers:
                self.peers[from_id] = PeerInfo(peer_id=from_id, name=from_name or "unknown", online=True)
            self.peers[from_id].last_seen = msg.get("ts", time.time())
            
            if self.ui:
                peer_list = [{"id": p.peer_id, "name": p.name, "online": p.online} for p in self.peers.values()]
                self.ui.update_peers(peer_list)

        try:
            msg["body"] = self.crypto.decrypt(
                msg.get("body", ""), 
                msg.get("enc", "none"), 
                from_id or ""
            )
        except Exception: pass
            
        if self.ui:
            ui_msg = {
                "id": msg.get("id"),
                "from_id": from_id,
                "from_name": from_name,
                "ts": msg.get("ts", time.time()),
                "body": msg.get("body"),
                "enc": msg.get("enc", "none"),
                "state": "sent",
                "channel": msg.get("channel") or ("@broadcast" if msg.get("type") == "broadcast" else None),
                "to": msg.get("to"),
            }
            self.ui.push_message(ui_msg)
            
        self.history.append(msg)
        return msg

    async def join_channel(self, name: str, password: str | None = None):
        if not name.startswith("@"): name = "@" + name
        self.channels.setdefault(name, set())
        self.active_channel = name
        await self.transport.join_channel(name, password)

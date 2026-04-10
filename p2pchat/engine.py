from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .crypto import CryptoManager
from .protocol import build


@dataclass
class PeerInfo:
    peer_id: str
    name: str
    connection: Any = None
    public_key: str | None = None


@dataclass
class Engine:
    identity: Any
    config: Any
    transport: Any
    crypto: CryptoManager
    peers: dict[str, PeerInfo] = field(default_factory=dict)
    channels: dict[str, set[str]] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    active_channel: str | None = None

    async def send_direct(self, peer_id: str, text: str):
        body, enc = self.crypto.encrypt(text, peer_id, self.config.crypto.default_mode)
        msg = build("msg", body, self.identity.peer_id, self.identity.username, peer_id, self.active_channel, enc)
        peer = self.peers.get(peer_id)
        connection = peer.connection if peer else self.transport.connections.get(peer_id)
        await self.transport.send(connection, msg)
        self.history.append(msg)
        return msg

    async def send_channel(self, channel: str, text: str):
        body, enc = self.crypto.encrypt(text, channel, self.config.crypto.default_mode)
        msg = build("msg", body, self.identity.peer_id, self.identity.username, channel, channel, enc)
        await self.transport.broadcast(msg)
        self.history.append(msg)
        return msg

    async def broadcast(self, text: str):
        body, enc = self.crypto.encrypt(text, "*", self.config.crypto.default_mode)
        msg = build("broadcast", body, self.identity.peer_id, self.identity.username, "*", None, enc)
        await self.transport.broadcast(msg)
        self.history.append(msg)
        return msg

    def on_message(self, msg: dict):
        msg = dict(msg)
        try:
            msg["plaintext"] = self.crypto.decrypt(msg.get("body", ""), msg.get("enc", "none"), msg.get("from_id", ""))
        except Exception:
            msg["plaintext"] = msg.get("body", "")
        self.history.append(msg)
        return msg

    async def join_channel(self, name: str):
        self.channels.setdefault(name, set())
        self.active_channel = name
        await self.transport.join_channel(name)

    async def leave_channel(self, name: str):
        self.channels.pop(name, None)
        if self.active_channel == name:
            self.active_channel = None
        await self.transport.leave_channel(name)

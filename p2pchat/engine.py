from __future__ import annotations

import logging
import asyncio
import time
import uuid
import os
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path

from .crypto import CryptoManager
from .protocol import build
from .storage import Storage

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
    home: Path = field(default_factory=lambda: Path.home() / ".p2pchat")
    peers: dict[str, PeerInfo] = field(default_factory=dict)
    channels: dict[str, set[str]] = field(default_factory=dict)
    active_channel: str | None = "@broadcast"
    ui: Any = None
    loop: asyncio.AbstractEventLoop | None = None
    storage: Storage = field(init=False)

    def __post_init__(self):
        self.storage = Storage(self.home)

    def set_ui(self, ui: Any):
        self.ui = ui
        if self.active_channel:
            msgs = self.storage.get_messages(self.active_channel)
            for m in msgs:
                self.ui.push_message(m)

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    async def send_direct(self, peer_id: str, text: str, msg_id: str | None = None):
        """Send a direct message to a peer with error handling."""
        try:
            mid = msg_id or str(uuid.uuid4())

            if not peer_id:
                logger.error("send_direct: empty peer_id")
                return None

            if not text:
                logger.warning("send_direct: empty message text")
                return None

            body, enc = self.crypto.encrypt(
                text, peer_id, self.config.crypto.default_mode
            )

            msg = build(
                type_="msg",
                body=body,
                from_id=self.identity.peer_id,
                from_name=self.identity.username,
                to=peer_id,
                channel=None,
                enc=enc,
                ts=time.time(),
            )
            msg["id"] = mid
            msg["state"] = "sending"
            self.storage.save_message(msg)

            ok = await self.transport.send(peer_id, msg)
            state = "sent" if ok else "failed"
            self.storage.update_message_state(mid, state)
            if self.ui:
                self.ui.set_message_state(mid, state)

            if not ok:
                logger.warning(f"send_direct failed to {peer_id}")

            return msg
        except Exception as e:
            logger.error(f"send_direct error: {e}")
            if msg_id and self.ui:
                self.ui.set_message_state(msg_id, "failed")
            return None

    async def send_channel(self, channel: str, text: str, msg_id: str | None = None):
        """Send a message to a channel with error handling."""
        try:
            mid = msg_id or str(uuid.uuid4())

            if not channel:
                logger.error("send_channel: empty channel")
                return None

            if not text:
                logger.warning("send_channel: empty message text")
                return None

            body, enc = self.crypto.encrypt(
                text, channel, self.config.crypto.default_mode
            )

            msg = build(
                type_="msg",
                body=body,
                from_id=self.identity.peer_id,
                from_name=self.identity.username,
                to=channel,
                channel=channel,
                enc=enc,
                ts=time.time(),
            )
            msg["id"] = mid
            msg["state"] = "sending"

            self.storage.save_message(msg)

            ok = await self.transport.send(channel, msg)
            state = "sent" if ok else "failed"
            self.storage.update_message_state(mid, state)
            if self.ui:
                self.ui.set_message_state(mid, state)

            if not ok:
                logger.warning(f"send_channel failed to {channel}")

            return msg
        except Exception as e:
            logger.error(f"send_channel error: {e}")
            if msg_id and self.ui:
                self.ui.set_message_state(msg_id, "failed")
            return None

    async def broadcast(self, text: str, msg_id: str | None = None):
        """Send a broadcast message with error handling."""
        try:
            mid = msg_id or str(uuid.uuid4())

            if not text:
                logger.warning("broadcast: empty message text")
                return None

            body, enc = self.crypto.encrypt(text, "*", self.config.crypto.default_mode)

            msg = build(
                type_="broadcast",
                body=body,
                from_id=self.identity.peer_id,
                from_name=self.identity.username,
                to="*",
                channel=None,
                enc=enc,
                ts=time.time(),
            )
            msg["id"] = mid
            msg["state"] = "sending"

            self.storage.save_message(msg)

            ok = await self.transport.broadcast(msg)
            state = "sent" if ok else "failed"
            self.storage.update_message_state(mid, state)
            if self.ui:
                self.ui.set_message_state(mid, state)

            if not ok:
                logger.warning("broadcast failed")

            return msg
        except Exception as e:
            logger.error(f"broadcast error: {e}")
            if msg_id and self.ui:
                self.ui.set_message_state(msg_id, "failed")
            return None

    def on_message(self, msg: dict):
        if not msg:
            return
        msg = dict(msg)
        from_id = msg.get("from_id")
        from_name = msg.get("from_name")
        msg_type = msg.get("type", "msg")

        if msg_type == "file-offer":
            asyncio.create_task(self._handle_file_offer(msg))
            return

        if from_id:
            if from_id not in self.peers:
                self.peers[from_id] = PeerInfo(
                    peer_id=from_id, name=from_name or "unknown", online=True
                )
            self.peers[from_id].last_seen = msg.get("ts", time.time())
            self.storage.save_peer({"id": from_id, "name": from_name, "online": True})

            if self.ui:
                peer_list = [
                    {"id": p.peer_id, "name": p.name, "online": p.online}
                    for p in self.peers.values()
                ]
                self.ui.update_peers(peer_list)

        try:
            msg["body"] = self.crypto.decrypt(
                msg.get("body", ""), msg.get("enc", "none"), from_id or ""
            )
        except Exception:
            pass

        ui_msg = {
            "id": msg.get("id"),
            "from_id": from_id,
            "from_name": from_name,
            "ts": msg.get("ts", time.time()),
            "body": msg.get("body"),
            "enc": msg.get("enc", "none"),
            "state": "sent",
            "channel": msg.get("channel")
            or ("@broadcast" if msg.get("type") == "broadcast" else None),
            "to": msg.get("to"),
        }

        self.storage.save_message(ui_msg)

        if self.ui:
            self.ui.push_message(ui_msg)

        return msg

    async def join_channel(self, name: str, password: str | None = None):
        if not name.startswith("@"):
            name = "@" + name
        self.channels.setdefault(name, set())
        self.active_channel = name

        if self.ui:
            msgs = self.storage.get_messages(name)
            for m in msgs:
                self.ui.push_message(m)

        await self.transport.join_channel(name, password)

    async def offer_file(self, peer_id: str, file_path: str):
        path = Path(file_path)
        if not path.exists():
            return False

        file_name = path.name
        file_size = path.stat().st_size
        transfer_id = str(uuid.uuid4())

        self.storage.create_file_transfer(
            transfer_id, file_name, str(path), file_size, "send", peer_id
        )

        offer = {
            "type": "file-offer",
            "transfer_id": transfer_id,
            "file_name": file_name,
            "file_size": file_size,
            "from_id": self.identity.peer_id,
            "from_name": self.identity.username,
        }
        await self.transport.send(peer_id, offer)
        return True

    async def _handle_file_offer(self, msg: dict):
        transfer_id = msg["transfer_id"]
        file_name = msg["file_name"]
        file_size = msg["file_size"]
        peer_id = msg["from_id"]

        download_dir = self.home / "downloads"
        download_dir.mkdir(exist_ok=True)
        save_path = download_dir / file_name

        self.storage.create_file_transfer(
            transfer_id, file_name, str(save_path), file_size, "receive", peer_id
        )

        accept = {
            "type": "file-accept",
            "transfer_id": transfer_id,
            "from_id": self.identity.peer_id,
        }
        await self.transport.send(peer_id, accept)

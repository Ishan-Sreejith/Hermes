from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("features")


class ReactionType(str, Enum):
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    HEART = "heart"
    LAUGH = "laugh"
    WOW = "wow"
    SAD = "sad"
    ANGRY = "angry"
    THINKING = "thinking"
    ROCKET = "rocket"
    EYES = "eyes"

    @classmethod
    def from_emoji(cls, emoji: str) -> "ReactionType | None":
        for rt in cls:
            if rt.value == emoji:
                return rt
        return None

    @classmethod
    def from_alias(cls, alias: str) -> "ReactionType | None":
        aliases = {
            "+1": cls.THUMBS_UP,
            "-1": cls.THUMBS_DOWN,
            "love": cls.HEART,
            "haha": cls.LAUGH,
            "wow": cls.WOW,
            "sad": cls.SAD,
            "angry": cls.ANGRY,
            "think": cls.THINKING,
            "rocket": cls.ROCKET,
            "eyes": cls.EYES,
            "like": cls.THUMBS_UP,
        }
        return aliases.get(alias.lower())


@dataclass
class Reaction:
    msg_id: str
    user_id: str
    user_name: str
    emoji: str
    ts: float = field(default_factory=time.time)


@dataclass
class Reactions:
    reactions: dict[str, list[Reaction]] = field(default_factory=dict)

    def add(self, msg_id: str, user_id: str, user_name: str, emoji: str) -> bool:
        if msg_id not in self.reactions:
            self.reactions[msg_id] = []

        existing = next(
            (r for r in self.reactions[msg_id] if r.user_id == user_id), None
        )
        if existing:
            existing.emoji = emoji
            existing.ts = time.time()
        else:
            self.reactions[msg_id].append(Reaction(msg_id, user_id, user_name, emoji))
        return True

    def remove(self, msg_id: str, user_id: str) -> bool:
        if msg_id in self.reactions:
            before = len(self.reactions[msg_id])
            self.reactions[msg_id] = [
                r for r in self.reactions[msg_id] if r.user_id != user_id
            ]
            return len(self.reactions[msg_id]) < before
        return False

    def get(self, msg_id: str) -> list[Reaction]:
        return self.reactions.get(msg_id, [])

    def get_counts(self, msg_id: str) -> dict[str, int]:
        reactions = self.get(msg_id)
        counts: dict[str, int] = {}
        for r in reactions:
            counts[r.emoji] = counts.get(r.emoji, 0) + 1
        return counts

    def to_dict(self, msg_id: str) -> dict:
        return {
            "counts": self.get_counts(msg_id),
            "users": [
                {"user_id": r.user_id, "user_name": r.user_name, "emoji": r.emoji}
                for r in self.get(msg_id)
            ],
        }


class TypingIndicator:
    def __init__(self, timeout_s: float = 3.0):
        self._typing: dict[str, dict[str, float]] = {}
        self._lock = asyncio.Lock()
        self._timeout_s = timeout_s

    async def start(self, channel: str, user_id: str, user_name: str):
        async with self._lock:
            if channel not in self._typing:
                self._typing[channel] = {}
            self._typing[channel][user_id] = time.time()

    async def stop(self, channel: str, user_id: str):
        async with self._lock:
            if channel in self._typing and user_id in self._typing[channel]:
                del self._typing[channel][user_id]

    async def get_typing(self, channel: str) -> list[dict]:
        async with self._lock:
            now = time.time()
            if channel not in self._typing:
                return []
            typing_users = []
            for user_id, ts in list(self._typing[channel].items()):
                if now - ts > self._timeout_s:
                    del self._typing[channel][user_id]
                else:
                    typing_users.append({"user_id": user_id, "ts": ts})
            return typing_users

    async def cleanup(self):
        async with self._lock:
            now = time.time()
            for channel in list(self._typing.keys()):
                for user_id in list(self._typing[channel].keys()):
                    if now - self._typing[channel][user_id] > self._timeout_s:
                        del self._typing[channel][user_id]


class ReadReceipts:
    def __init__(self):
        self._receipts: dict[str, dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def mark_read(self, channel: str, user_id: str, msg_id: str):
        async with self._lock:
            key = f"{channel}:{user_id}"
            if key not in self._receipts:
                self._receipts[key] = {}
            self._receipts[key][msg_id] = time.time()

    async def get_read_by(self, channel: str, msg_id: str) -> list[str]:
        async with self._lock:
            user_ids = []
            prefix = f"{channel}:"
            for key, msgs in self._receipts.items():
                if key.startswith(prefix) and msg_id in msgs:
                    user_ids.append(key[len(prefix) :])
            return user_ids

    async def get_last_read(self, channel: str, user_id: str) -> str | None:
        async with self._lock:
            key = f"{channel}:{user_id}"
            if key in self._receipts and self._receipts[key]:
                return max(self._receipts[key].items(), key=lambda x: x[1])[0]
        return None


@dataclass
class Message:
    id: str
    from_id: str
    from_name: str
    body: str
    ts: float
    channel: str | None = None
    to: str | None = None
    enc: str = "none"
    state: str = "sent"
    reactions: list[dict] = field(default_factory=list)
    edited: bool = False
    edited_ts: float | None = None
    reply_to: str | None = None
    attachments: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from_id": self.from_id,
            "from_name": self.from_name,
            "body": self.body,
            "ts": self.ts,
            "channel": self.channel,
            "to": self.to,
            "enc": self.enc,
            "state": self.state,
            "reactions": self.reactions,
            "edited": self.edited,
            "edited_ts": self.edited_ts,
            "reply_to": self.reply_to,
            "attachments": self.attachments,
        }


class MessageStore:
    def __init__(self, max_size: int = 1000):
        self._messages: list[Message] = []
        self._by_id: dict[str, Message] = {}
        self._by_channel: dict[str, list[str]] = {}
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def add(self, msg: Message):
        async with self._lock:
            self._messages.append(msg)
            self._by_id[msg.id] = msg
            if msg.channel:
                if msg.channel not in self._by_channel:
                    self._by_channel[msg.channel] = []
                self._by_channel[msg.channel].append(msg.id)

            while len(self._messages) > self._max_size:
                removed = self._messages.pop(0)
                if removed.id in self._by_id:
                    del self._by_id[removed.id]
                if removed.channel and removed.channel in self._by_channel:
                    if removed.id in self._by_channel[removed.channel]:
                        self._by_channel[removed.channel].remove(removed.id)

    async def get(self, msg_id: str) -> Message | None:
        return self._by_id.get(msg_id)

    async def get_by_channel(self, channel: str, limit: int = 100) -> list[Message]:
        async with self._lock:
            if channel not in self._by_channel:
                return []
            ids = self._by_channel[channel][-limit:]
            return [self._by_id[mid] for mid in ids if mid in self._by_id]

    async def search(self, query: str, channel: str | None = None) -> list[Message]:
        query_lower = query.lower()
        results = []
        async with self._lock:
            for msg in self._messages:
                if query_lower in msg.body.lower():
                    if channel is None or msg.channel == channel:
                        results.append(msg)
        return results[-100:]

    async def update(self, msg_id: str, **kwargs):
        async with self._lock:
            if msg_id in self._by_id:
                msg = self._by_id[msg_id]
                for key, value in kwargs.items():
                    if hasattr(msg, key):
                        setattr(msg, key, value)


@dataclass
class UserProfile:
    peer_id: str
    username: str
    display_name: str | None = None
    bio: str | None = None
    avatar_url: str | None = None
    status: str = "offline"
    status_message: str | None = None
    last_seen: float = field(default_factory=time.time)
    custom_fields: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "peer_id": self.peer_id,
            "username": self.username,
            "display_name": self.display_name,
            "bio": self.bio,
            "avatar_url": self.avatar_url,
            "status": self.status,
            "status_message": self.status_message,
            "last_seen": self.last_seen,
            "custom_fields": self.custom_fields,
        }


class ProfileManager:
    def __init__(self, storage_path: Path | None = None):
        self._profiles: dict[str, UserProfile] = {}
        self._storage_path = storage_path or Path.home() / ".p2pchat" / "profiles.json"
        self._lock = asyncio.Lock()
        self._load_sync()

    def _load_sync(self):
        if self._storage_path.exists():
            try:
                data = json.loads(self._storage_path.read_text())
                for peer_id, profile_data in data.items():
                    self._profiles[peer_id] = UserProfile(**profile_data)
            except Exception:
                pass

    async def _save_sync(self):
        data = {pid: p.to_dict() for pid, p in self._profiles.items()}
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(json.dumps(data, indent=2))

    async def get(self, peer_id: str) -> UserProfile | None:
        return self._profiles.get(peer_id)

    async def set(self, profile: UserProfile):
        async with self._lock:
            self._profiles[profile.peer_id] = profile
            await self._save_sync()

    async def update(self, peer_id: str, **kwargs):
        async with self._lock:
            if peer_id not in self._profiles:
                self._profiles[peer_id] = UserProfile(
                    peer_id=peer_id, username=kwargs.get("username", "unknown")
                )
            for key, value in kwargs.items():
                if hasattr(self._profiles[peer_id], key):
                    setattr(self._profiles[peer_id], key, value)
            self._profiles[peer_id].last_seen = time.time()
            await self._save_sync()


@dataclass
class ChannelMember:
    peer_id: str
    username: str
    role: str = "member"
    joined_at: float = field(default_factory=time.time)
    muted_until: float = 0
    is_banned: bool = False


class ChannelAdmin:
    def __init__(self):
        self._channels: dict[str, dict[str, ChannelMember]] = {}
        self._channel_info: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def create_channel(
        self, name: str, creator_id: str, creator_name: str, password: str | None = None
    ):
        async with self._lock:
            if name not in self._channels:
                self._channels[name] = {}
            member = ChannelMember(
                peer_id=creator_id, username=creator_name, role="owner"
            )
            self._channels[name][creator_id] = member
            self._channel_info[name] = {
                "name": name,
                "created_at": time.time(),
                "password_hash": hashlib.sha256(password.encode()).hexdigest()
                if password
                else None,
                "topic": None,
                "created_by": creator_id,
            }
            return True

    async def join_channel(
        self, name: str, peer_id: str, username: str, password: str | None = None
    ) -> bool:
        async with self._lock:
            if name not in self._channels:
                await self.create_channel(name, peer_id, username)

            info = self._channel_info.get(name, {})
            if info.get("password_hash"):
                if (
                    not password
                    or hashlib.sha256(password.encode()).hexdigest()
                    != info["password_hash"]
                ):
                    return False

            if peer_id in self._channels[name]:
                if self._channels[name][peer_id].is_banned:
                    return False
            else:
                self._channels[name][peer_id] = ChannelMember(
                    peer_id=peer_id, username=username
                )
            return True

    async def leave_channel(self, name: str, peer_id: str):
        async with self._lock:
            if name in self._channels and peer_id in self._channels[name]:
                member = self._channels[name][peer_id]
                if member.role == "owner":
                    if len(self._channels[name]) > 1:
                        return False
                    del self._channels[name]
                    if name in self._channel_info:
                        del self._channel_info[name]
                else:
                    del self._channels[name][peer_id]
            return True

    async def kick(self, channel: str, admin_id: str, target_id: str) -> bool:
        async with self._lock:
            if channel not in self._channels:
                return False
            if admin_id not in self._channels[channel]:
                return False
            admin = self._channels[channel][admin_id]
            if admin.role not in ("owner", "admin"):
                return False
            if target_id in self._channels[channel]:
                del self._channels[channel][target_id]
                return True
            return False

    async def ban(self, channel: str, admin_id: str, target_id: str) -> bool:
        async with self._lock:
            if channel not in self._channels:
                return False
            if admin_id not in self._channels[channel]:
                return False
            admin = self._channels[channel][admin_id]
            if admin.role not in ("owner", "admin"):
                return False
            if target_id in self._channels[channel]:
                self._channels[channel][target_id].is_banned = True
                return True
            return False

    async def mute(
        self, channel: str, admin_id: str, target_id: str, duration_s: int
    ) -> bool:
        async with self._lock:
            if channel not in self._channels:
                return False
            if admin_id not in self._channels[channel]:
                return False
            admin = self._channels[channel][admin_id]
            if admin.role not in ("owner", "admin"):
                return False
            if target_id in self._channels[channel]:
                self._channels[channel][target_id].muted_until = (
                    time.time() + duration_s
                )
                return True
            return False

    async def unmute(self, channel: str, admin_id: str, target_id: str) -> bool:
        return await self.mute(channel, admin_id, target_id, 0)

    async def set_role(
        self, channel: str, admin_id: str, target_id: str, role: str
    ) -> bool:
        async with self._lock:
            if channel not in self._channels:
                return False
            if admin_id not in self._channels[channel]:
                return False
            admin = self._channels[channel][admin_id]
            if admin.role != "owner":
                return False
            if target_id in self._channels[channel]:
                self._channels[channel][target_id].role = role
                return True
            return False

    async def get_members(self, channel: str) -> list[ChannelMember]:
        async with self._lock:
            return list(self._channels.get(channel, {}).values())

    async def is_muted(self, channel: str, peer_id: str) -> bool:
        async with self._lock:
            if channel not in self._channels:
                return False
            if peer_id not in self._channels[channel]:
                return False
            member = self._channels[channel][peer_id]
            return member.muted_until > time.time()

    async def set_topic(self, channel: str, setter_id: str, topic: str):
        async with self._lock:
            if channel not in self._channel_info:
                return False
            if channel in self._channels and setter_id in self._channels[channel]:
                self._channel_info[channel]["topic"] = topic
                return True
            return False


class MarkdownFormatter:
    @staticmethod
    def format(text: str) -> str:
        text = MarkdownFormatter._escape_html(text)
        text = MarkdownFormatter._bold(text)
        text = MarkdownFormatter._italic(text)
        text = MarkdownFormatter._code(text)
        text = MarkdownFormatter._codeblock(text)
        text = MarkdownFormatter._links(text)
        text = MarkdownFormatter._strikethrough(text)
        return text

    @staticmethod
    def _escape_html(text: str) -> str:
        replacements = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    @staticmethod
    def _bold(text: str) -> str:
        pattern = r"\*\*(.+?)\*\*"
        return re.sub(pattern, r"<strong>\1</strong>", text)

    @staticmethod
    def _italic(text: str) -> str:
        pattern = r"\*(.+?)\*"
        return re.sub(pattern, r"<em>\1</em>", text)

    @staticmethod
    def _code(text: str) -> str:
        pattern = r"`(.+?)`"
        return re.sub(pattern, r"<code>\1</code>", text)

    @staticmethod
    def _codeblock(text: str) -> str:
        pattern = r"```(\w*)\n(.+?)```"
        return re.sub(
            pattern, r'<pre class="\1"><code>\2</code></pre>', text, flags=re.DOTALL
        )

    @staticmethod
    def _links(text: str) -> str:
        pattern = r"\[([^\]]+)\]\(([^)]+)\)"
        return re.sub(pattern, r'<a href="\2">\1</a>', text)

    @staticmethod
    def _strikethrough(text: str) -> str:
        pattern = r"~~(.+?)~~"
        return re.sub(pattern, r"<del>\1</del>", text)


class FileHandler:
    MAX_FILE_SIZE = 10 * 1024 * 1024

    @staticmethod
    def encode_file(path: Path) -> tuple[str, str] | None:
        if not path.exists():
            return None
        if path.stat().st_size > FileHandler.MAX_FILE_SIZE:
            return None
        try:
            data = path.read_bytes()
            encoded = base64.b64encode(data).decode("ascii")
            hash_val = hashlib.sha256(data).hexdigest()
            return encoded, hash_val
        except Exception:
            return None

    @staticmethod
    def decode_file(
        encoded: str, hash_expected: str | None = None
    ) -> tuple[bytes, str] | None:
        try:
            data = base64.b64decode(encoded.encode("ascii"))
            hash_val = hashlib.sha256(data).hexdigest()
            if hash_expected and hash_val != hash_expected:
                return None
            return data, hash_val
        except Exception:
            return None

    @staticmethod
    def get_mime_type(filename: str) -> str:
        ext_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
            ".txt": "text/plain",
            ".json": "application/json",
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
        }
        ext = Path(filename).suffix.lower()
        return ext_map.get(ext, "application/octet-stream")

    @staticmethod
    def format_size(size_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

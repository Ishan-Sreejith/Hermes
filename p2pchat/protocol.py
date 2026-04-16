from __future__ import annotations

import asyncio
import json
import struct
import time
import uuid
import logging
from typing import Any

FRAME_LEN = 4
PROTOCOL_VERSION = 2

logger = logging.getLogger("protocol")


def build(
    type_: str,
    body: str,
    from_id: str | None = None,
    from_name: str | None = None,
    to: str | None = None,
    channel: str | None = None,
    enc: str = "none",
    **kwargs,
) -> dict[str, Any]:
    now = time.time()
    scope = "public"
    if to and not to.startswith("@") and to != "*":
        scope = "private"

    msg = {
        "v": PROTOCOL_VERSION,
        "id": str(uuid.uuid4()),
        "type": type_,
        "channel": channel,
        "from_id": from_id,
        "fromId": from_id,
        "from_name": from_name,
        "fromName": from_name,
        "to": to,
        "ts": now,
        "enc": enc,
        "body": body,
        "text": body,
        "scope": scope,
        "source": "terminal",
    }
    msg.update(kwargs)
    return msg


def encode(msg: dict[str, Any]) -> bytes:
    payload = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(payload)) + payload


def decode_frame(data: bytes) -> dict[str, Any]:
    if len(data) < 4:
        return {}
    try:
        length = struct.unpack(">I", data[:4])[0]
        payload = data[4 : 4 + length]
        if len(payload) < length:
            return {}
        return json.loads(payload.decode("utf-8"))
    except (struct.error, json.JSONDecodeError, UnicodeDecodeError):
        return {}


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    try:
        first_byte = await reader.read(1)
        if not first_byte:
            return None

        if first_byte != b"\x00":
            rest = await reader.read(4000)
            text = (first_byte + rest).decode("utf-8", errors="replace").strip()
            if not text:
                return None
            return {
                "v": PROTOCOL_VERSION,
                "type": "msg",
                "body": text,
                "text": text,
                "from_id": "raw-nc",
                "from_name": "nc-user",
                "ts": time.time(),
                "source": "nc",
            }

        remaining_header = await reader.readexactly(FRAME_LEN - 1)
        header = first_byte + remaining_header
        length = struct.unpack(">I", header)[0]

        if length > 10 * 1024 * 1024:
            logger.error("Message frame too large")
            return None

        payload = await reader.readexactly(length)
        return json.loads(payload.decode("utf-8"))
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    except Exception as e:
        logger.debug(f"Protocol read error: {e}")
        return None


async def write_message(writer: asyncio.StreamWriter, msg: dict[str, Any]) -> bool:
    try:
        data = encode(msg)
        writer.write(data)
        await writer.drain()
        return True
    except (ConnectionError, OSError, asyncio.CancelledError):
        return False

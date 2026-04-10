from __future__ import annotations

import asyncio
import json
import struct
import time
import uuid
from typing import Any

FRAME_LEN = 4
PROTOCOL_VERSION = 1


def build(type_: str, body: str, from_id: str | None = None, from_name: str | None = None, to: str | None = None, channel: str | None = None, enc: str = "none") -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "id": str(uuid.uuid4()),
        "type": type_,
        "channel": channel,
        "from_id": from_id,
        "from_name": from_name,
        "to": to,
        "ts": time.time(),
        "enc": enc,
        "body": body,
    }


def encode(msg: dict[str, Any]) -> bytes:
    payload = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(payload)) + payload


def decode_frame(data: bytes) -> dict[str, Any]:
    if len(data) < FRAME_LEN:
        raise ValueError("frame too short")
    length = struct.unpack(">I", data[:FRAME_LEN])[0]
    payload = data[FRAME_LEN : FRAME_LEN + length]
    if len(payload) != length:
        raise ValueError("incomplete frame")
    return json.loads(payload.decode("utf-8"))


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    try:
        header = await reader.readexactly(FRAME_LEN)
        if not header:
            return None
        length = struct.unpack(">I", header)[0]
        if length > 10 * 1024 * 1024:
            raise ValueError("frame too large")
        payload = await reader.readexactly(length)
        return json.loads(payload.decode("utf-8"))
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ValueError, json.JSONDecodeError):
        return None


async def write_message(writer: asyncio.StreamWriter, msg: dict[str, Any]) -> bool:
    try:
        writer.write(encode(msg))
        await writer.drain()
        return True
    except (ConnectionError, OSError, asyncio.TimeoutError):
        return False

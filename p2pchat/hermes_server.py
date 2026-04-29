from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

from .protocol import read_message, write_message

logging.basicConfig(
    level=getattr(logging, os.getenv("HERMES_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("hermes_server")

DATA_DIR = Path.home() / ".hermes_server"
DATA_DIR.mkdir(exist_ok=True)
USER_DB_PATH = DATA_DIR / "users.json"
USERNAME_MAX_LEN = 64
CHANNEL_MAX_LEN = 64


def _is_non_empty_text(value: object, *, max_len: int | None = None) -> bool:
    if not isinstance(value, str):
        return False
    if not value.strip():
        return False
    if max_len is not None and len(value) > max_len:
        return False
    return True


def load_db() -> dict:
    if not USER_DB_PATH.exists():
        return {}
    try:
        raw = json.loads(USER_DB_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load user DB (%s); starting empty DB", e)
        return {}

    if not isinstance(raw, dict):
        logger.warning("Invalid user DB format; expected object")
        return {}

    cleaned: dict[str, dict[str, str]] = {}
    for username, data in raw.items():
        if not isinstance(username, str) or not isinstance(data, dict):
            continue
        password_hash = data.get("password")
        peer_id = data.get("peer_id")
        if isinstance(password_hash, str) and isinstance(peer_id, str):
            cleaned[username] = {"password": password_hash, "peer_id": peer_id}
    if len(cleaned) != len(raw):
        logger.warning("Skipped malformed entries while loading user DB")
    return cleaned


def save_db(db: dict):
    tmp = USER_DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(USER_DB_PATH)


def _safe_str(value: object, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _normalize_channel(name: str) -> str:
    stripped = name.strip()
    if not stripped.startswith("@"):
        stripped = f"@{stripped}"
    return stripped


async def _write_error(writer: asyncio.StreamWriter, message: str) -> None:
    await write_message(writer, {"type": "error", "message": message})


async def _write_auth_error(writer: asyncio.StreamWriter, message: str) -> None:
    await write_message(writer, {"type": "auth_res", "ok": False, "error": message})


def _hash_password(raw_password: str) -> str:
    return sha256(raw_password.encode()).hexdigest()


def _generate_peer_id() -> str:
    return f"user_{os.urandom(4).hex()}"


def _prune_empty_channels() -> None:
    for channel in [name for name, members in channels.items() if not members]:
        channels.pop(channel, None)
        channel_passwords.pop(channel, None)


connections: dict[str, asyncio.StreamWriter] = {}
channels: dict[str, set[str]] = defaultdict(set)
channel_passwords: dict[str, str] = {}


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer_id = None
    username = None
    peer_addr = writer.get_extra_info("peername")
    logger.info(f"New connection from {peer_addr}")

    try:
        while True:
            msg = await read_message(reader)
            if msg is None:
                break
            if not isinstance(msg, dict):
                continue

            m_type = msg.get("type")

            if m_type == "auth":
                u = msg.get("username")
                p = msg.get("password")
                if not _is_non_empty_text(u, max_len=USERNAME_MAX_LEN):
                    await _write_auth_error(writer, "Invalid username")
                    continue
                if not _is_non_empty_text(p, max_len=256):
                    await _write_auth_error(writer, "Invalid password")
                    continue
                p = _safe_str(p)
                hashed = _hash_password(p)

                db = load_db()
                username = _safe_str(u).strip()

                if username in db:
                    if hmac.compare_digest(db[username]["password"], hashed):
                        peer_id = db[username]["peer_id"]
                        connections[peer_id] = writer
                        await write_message(
                            writer,
                            {
                                "type": "auth_res",
                                "ok": True,
                                "peer_id": peer_id,
                                "username": username,
                            },
                        )
                        logger.info(f"User {username} logged in ({peer_id})")
                    else:
                        await _write_auth_error(writer, "Invalid password")
                else:
                    requested_peer_id = msg.get("peer_id")
                    peer_id = (
                        requested_peer_id
                        if _is_non_empty_text(requested_peer_id, max_len=128)
                        else _generate_peer_id()
                    )
                    db[username] = {"password": hashed, "peer_id": peer_id}
                    save_db(db)
                    connections[peer_id] = writer
                    await write_message(
                        writer,
                        {
                            "type": "auth_res",
                            "ok": True,
                            "peer_id": peer_id,
                            "username": username,
                        },
                    )
                    logger.info(f"New user {username} registered ({peer_id})")

            elif m_type == "join":
                chan = msg.get("channel")
                pwd = msg.get("password")
                if not peer_id:
                    continue
                if not _is_non_empty_text(chan, max_len=CHANNEL_MAX_LEN):
                    await _write_error(writer, "Invalid channel name")
                    continue
                if pwd is not None and not _is_non_empty_text(pwd, max_len=256):
                    await _write_error(writer, "Invalid channel password")
                    continue
                chan = _normalize_channel(_safe_str(chan))
                pwd = _safe_str(pwd, "")

                if chan in channel_passwords:
                    if not hmac.compare_digest(channel_passwords[chan], pwd):
                        await _write_error(writer, f"Wrong password for {chan}")
                        continue
                elif pwd:
                    channel_passwords[chan] = pwd

                channels[chan].add(peer_id)
                logger.info(f"Peer {peer_id} joined {chan}")

            elif m_type == "relay":
                target = msg.get("to")
                body = msg.get("body")
                if not peer_id or not isinstance(body, dict):
                    continue

                if target == "@broadcast" or target == "*":
                    for pid, w in list(connections.items()):
                        if pid != peer_id:
                            await write_message(w, body)

                elif isinstance(target, str) and target.startswith("@"):
                    if peer_id not in channels.get(target, set()):
                        if target not in channel_passwords:
                            channels[target].add(peer_id)
                        else:
                            continue

                    for pid in list(channels.get(target, set())):
                        if pid in connections and pid != peer_id:
                            await write_message(connections[pid], body)

                elif isinstance(target, str) and target in connections:
                    try:
                        await write_message(connections[target], body)
                    except Exception:
                        connections.pop(target, None)

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if peer_id:
            connections.pop(peer_id, None)
            for s in channels.values():
                s.discard(peer_id)
            _prune_empty_channels()
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def amain(host: str, port: int):
    server = await asyncio.start_server(handle_client, host, port)
    logger.info(f"Hermes server on {host}:{port}")
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    args = parser.parse_args()
    if args.version:
        print("0.3.3")
        return
    asyncio.run(amain(args.host, args.port))


if __name__ == "__main__":
    main()

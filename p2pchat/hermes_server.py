from __future__ import annotations

import argparse
import asyncio
import logging
import json
import os
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

from .protocol import read_message, write_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hermes_server")

DATA_DIR = Path.home() / ".hermes_server"
DATA_DIR.mkdir(exist_ok=True)
USER_DB_PATH = DATA_DIR / "users.json"

def load_db() -> dict:
    if USER_DB_PATH.exists():
        return json.loads(USER_DB_PATH.read_text())
    return {}

def save_db(db: dict):
    USER_DB_PATH.write_text(json.dumps(db, indent=2))

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
            if msg is None: break
            
            m_type = msg.get("type")
            
            if m_type == "auth":
                u = msg.get("username")
                p = msg.get("password")
                db = load_db()
                hashed = sha256(p.encode()).hexdigest()
                
                if u in db:
                    if db[u]["password"] == hashed:
                        peer_id = db[u]["peer_id"]
                        username = u
                        connections[peer_id] = writer
                        await write_message(writer, {"type": "auth_res", "ok": True, "peer_id": peer_id, "username": u})
                        logger.info(f"User {u} logged in ({peer_id})")
                    else:
                        await write_message(writer, {"type": "auth_res", "ok": False, "error": "Invalid password"})
                else:
                    peer_id = msg.get("peer_id") or f"user_{os.urandom(4).hex()}"
                    db[u] = {"password": hashed, "peer_id": peer_id}
                    save_db(db)
                    username = u
                    connections[peer_id] = writer
                    await write_message(writer, {"type": "auth_res", "ok": True, "peer_id": peer_id, "username": u})
                    logger.info(f"New user {u} registered ({peer_id})")

            elif m_type == "join":
                chan = msg.get("channel")
                pwd = msg.get("password")
                if not peer_id: continue
                
                if chan in channel_passwords:
                    if channel_passwords[chan] != pwd:
                        await write_message(writer, {"type": "error", "message": f"Wrong password for {chan}"})
                        continue
                elif pwd:
                    channel_passwords[chan] = pwd
                
                channels[chan].add(peer_id)
                logger.info(f"Peer {peer_id} joined {chan}")

            elif m_type == "relay":
                target = msg.get("to")
                body = msg.get("body")
                if not peer_id or not isinstance(body, dict): continue
                
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
                    await write_message(connections[target], body)

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if peer_id:
            connections.pop(peer_id, None)
            for s in channels.values(): s.discard(peer_id)
        writer.close()

async def amain(host: str, port: int):
    server = await asyncio.start_server(handle_client, host, port)
    logger.info(f"Hermes server on {host}:{port}")
    async with server: await server.serve_forever()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7777)
    args = parser.parse_args()
    asyncio.run(amain(args.host, args.port))

if __name__ == "__main__":
    main()

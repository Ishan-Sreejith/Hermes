from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from .protocol import read_message, write_message

connections: dict[str, asyncio.StreamWriter] = {}
channels: dict[str, set[str]] = defaultdict(set)


def reset_state() -> None:
    connections.clear()
    channels.clear()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    peer_id = None
    try:
        while msg := await read_message(reader):
            msg_type = msg.get("type")

            if msg_type == "register":
                peer_id = msg.get("peer_id")
                if not peer_id:
                    continue
                connections[peer_id] = writer
                for ch in msg.get("channels", []):
                    channels[ch].add(peer_id)

            elif msg_type == "join":
                if peer_id:
                    channels[msg["channel"]].add(peer_id)

            elif msg_type == "leave":
                if peer_id:
                    channels[msg["channel"]].discard(peer_id)

            elif msg_type == "relay":
                target = msg.get("to")
                body = msg.get("body")
                if not isinstance(body, dict):
                    continue

                if target == "*":
                    for pid, w in list(connections.items()):
                        if pid != peer_id:
                            await write_message(w, body)

                elif isinstance(target, str) and target.startswith("#"):
                    for pid in list(channels.get(target, set())):
                        if pid in connections and pid != peer_id:
                            await write_message(connections[pid], body)

                elif isinstance(target, str) and target in connections and target != peer_id:
                    await write_message(connections[target], body)

    finally:
        if peer_id and peer_id in connections:
            connections.pop(peer_id, None)
            for peers in channels.values():
                peers.discard(peer_id)
        writer.close()
        await writer.wait_closed()


async def amain(host: str, port: int):
    server = await asyncio.start_server(handle_client, host, port)
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7777)
    args = parser.parse_args()
    asyncio.run(amain(args.host, args.port))


if __name__ == "__main__":
    main()

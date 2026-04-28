from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger("transport")


@dataclass
class Connection:
    peer_id: str | None = None
    kind: str = "firebase_rest"
    ip: str | None = None
    port: int | None = None


class FirebaseTransport:
    def __init__(self, config: Any, identity: Any):
        self.config = config
        self.identity = identity
        self.db_url = (
            getattr(getattr(config, "cloud", None), "database_url", None)
            or os.getenv("HERMES_FIREBASE_DB_URL")
            or ""
        )
        self._seen_ids = set()
        self._last_key_by_path: dict[str, str] = {}
        self._active_tasks = {}
        self._next_seq_by_path: dict[str, int] = {}
        self._seq_by_path_key: dict[str, dict[str, int]] = {}
        self._seen_count_by_path: dict[str, int] = {}
        self._last_reconcile_ts_by_path: dict[str, float] = {}
        self._last_ts_by_path: dict[str, float] = {}

    def _to_seconds(self, raw_ts: Any) -> float:
        try:
            ts = float(raw_ts)
        except (TypeError, ValueError):
            return time.time()
        if ts <= 0:
            return time.time()
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        return ts

    def _request(
        self,
        path: str,
        method: str = "GET",
        data: Any = None,
        params: dict | None = None,
    ) -> Any:
        if not self.db_url:
            logger.error("Firebase database URL is not configured")
            return None
        url = f"{self.db_url}/{path}.json"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        try:
            req_data = json.dumps(data).encode("utf-8") if data is not None else None
            req = urllib.request.Request(url, data=req_data, method=method)
            req.add_header("Content-Type", "application/json")

            with urllib.request.urlopen(req, timeout=10) as response:
                res_body = response.read().decode("utf-8")
                return json.loads(res_body) if res_body else {}
        except Exception as e:
            logger.error("Firebase REST error at %s: %s", path, e)
            return None

    async def authenticate(self, username, password) -> dict:
        hashed = sha256(password.encode()).hexdigest()
        data = self._request(f"users/{username}")

        if data and isinstance(data, dict) and "password" in data:
            if data.get("password") == hashed:
                return {"ok": True, "peer_id": data["peer_id"], "username": username}
            return {"ok": False, "error": "Invalid password"}

        peer_id = str(uuid4())
        res = self._request(
            f"users/{username}",
            method="PUT",
            data={"password": hashed, "peer_id": peer_id},
        )
        if res is None:
            return {"ok": False, "error": "Network error during registration"}
        return {"ok": True, "peer_id": peer_id, "username": username}

    async def join_room(self, room_name, password=None) -> dict:
        safe_name = room_name.replace("@", "").lower()
        room_data = self._request(f"rooms/{safe_name}")

        hashed = sha256(password.encode()).hexdigest() if password else None
        if room_data and isinstance(room_data, dict) and room_data.get("password"):
            if room_data["password"] != hashed:
                return {"ok": False, "error": f"Wrong password for {room_name}"}
        elif not room_data:
            self._request(
                f"rooms/{safe_name}",
                method="PUT",
                data={"password": hashed, "created_at": time.time()},
            )

        path = (
            f"messages/chan_{safe_name}"
            if safe_name != "broadcast"
            else "messages/broadcast"
        )
        return {"ok": True, "path": path}

    async def list_rooms(self) -> list[str]:
        data = self._request("rooms")
        if not isinstance(data, dict):
            return []
        names = []
        for key in sorted(data.keys()):
            if key == "broadcast":
                names.append("@broadcast")
            else:
                names.append(f"@{key}")
        return names

    async def delete_room(self, room_name: str) -> bool:
        safe_name = str(room_name or "").replace("@", "").strip().lower()
        if not safe_name or safe_name == "broadcast":
            return False
        self._request(f"rooms/{safe_name}", method="DELETE")
        self._request(f"messages/chan_{safe_name}", method="DELETE")
        return True

    async def rename_room(self, old_name: str, new_name: str) -> bool:
        old_safe = str(old_name or "").replace("@", "").strip().lower()
        new_safe = str(new_name or "").replace("@", "").strip().lower()
        if (
            not old_safe
            or not new_safe
            or old_safe == "broadcast"
            or new_safe == "broadcast"
        ):
            return False
        if old_safe == new_safe:
            return True

        old_room = self._request(f"rooms/{old_safe}")
        if old_room is None:
            return False

        old_messages = self._request(f"messages/chan_{old_safe}")
        self._request(f"rooms/{new_safe}", method="PUT", data=old_room)
        if isinstance(old_messages, dict):
            self._request(f"messages/chan_{new_safe}", method="PUT", data=old_messages)
        self._request(f"rooms/{old_safe}", method="DELETE")
        self._request(f"messages/chan_{old_safe}", method="DELETE")
        return True

    def _mark_seen(self, path: str, key: str):
        marker = f"{path}:{key}"
        if marker not in self._seen_ids:
            self._seen_ids.add(marker)
            self._seen_count_by_path[path] = self._seen_count_by_path.get(path, 0) + 1
        self._last_key_by_path[path] = key

    def _is_seen(self, path: str, key: str) -> bool:
        return f"{path}:{key}" in self._seen_ids

    def _assign_seq(self, path: str, msg_key: str) -> int:
        path_map = self._seq_by_path_key.setdefault(path, {})
        existing = path_map.get(msg_key)
        if existing is not None:
            return existing

        next_seq = self._next_seq_by_path.get(path, 1)
        path_map[msg_key] = next_seq
        self._next_seq_by_path[path] = next_seq + 1
        return next_seq

    def _normalize_msg(self, path: str, msg_key: str, raw_msg: Any) -> dict | None:
        if not isinstance(raw_msg, dict):
            return None
        msg = dict(raw_msg)
        if msg.get("fromId") and not msg.get("from_id"):
            msg["from_id"] = msg.get("fromId")
        if msg.get("fromName") and not msg.get("from_name"):
            msg["from_name"] = msg.get("fromName")
        if msg.get("text") and not msg.get("body"):
            msg["body"] = msg.get("text")
        if msg.get("from_id") and not msg.get("fromId"):
            msg["fromId"] = msg.get("from_id")
        if msg.get("from_name") and not msg.get("fromName"):
            msg["fromName"] = msg.get("from_name")
        if msg.get("body") and not msg.get("text"):
            msg["text"] = msg.get("body")
        if not msg.get("id"):
            msg["id"] = msg_key
        msg["ts"] = self._to_seconds(msg.get("ts"))
        msg["_seq"] = self._assign_seq(path, msg_key)
        msg["_path"] = path
        msg["_firebase_key"] = msg_key
        self._mark_seen(path, msg_key)
        current_last_ts = self._last_ts_by_path.get(path, 0.0)
        if msg["ts"] > current_last_ts:
            self._last_ts_by_path[path] = msg["ts"]
        return msg

    def _count_remote_messages(self, path: str) -> int | None:
        data = self._request(path, params={"shallow": "true"})
        if isinstance(data, dict):
            return len(data)
        return None

    def _maybe_reconcile_gap(self, path: str, limit: int = 300) -> list[dict]:
        now = time.time()
        last = self._last_reconcile_ts_by_path.get(path, 0.0)
        if now - last < 2.0:
            return []

        self._last_reconcile_ts_by_path[path] = now
        remote_count = self._count_remote_messages(path)
        if remote_count is None:
            return []

        seen_count = self._seen_count_by_path.get(path, 0)
        if remote_count <= seen_count:
            return []

        catchup_limit = min(max(limit, 100), 600)
        data = self._request(
            path,
            params={"orderBy": '"$key"', "limitToLast": catchup_limit},
        )
        msgs = []
        if isinstance(data, dict):
            for msg_id in sorted(data.keys()):
                if self._is_seen(path, msg_id):
                    continue
                msg = self._normalize_msg(path, msg_id, data[msg_id])
                if msg is not None:
                    msgs.append(msg)
        return msgs

    def _prepare_outgoing_msg(self, path: str, msg: dict) -> dict:
        out = dict(msg or {})
        out.setdefault("id", str(uuid4()))
        out.setdefault("ts", time.time())
        out.setdefault("enc", "none")
        out.setdefault("v", 2)
        out.setdefault("source", "cli")

        if not out.get("to"):
            if path.endswith("/broadcast"):
                out["to"] = "@broadcast"
            elif "/chan_" in path:
                channel = path.rsplit("/chan_", 1)[-1]
                out["to"] = f"@{channel}"
            elif "/messages/" in path:
                out["to"] = path.rsplit("/messages/", 1)[-1]
        if out.get("to") == "*":
            out["to"] = "@broadcast"

        if out.get("from_id") and not out.get("fromId"):
            out["fromId"] = out.get("from_id")
        if out.get("fromId") and not out.get("from_id"):
            out["from_id"] = out.get("fromId")
        if out.get("from_name") and not out.get("fromName"):
            out["fromName"] = out.get("from_name")
        if out.get("fromName") and not out.get("from_name"):
            out["from_name"] = out.get("fromName")
        if out.get("body") and not out.get("text"):
            out["text"] = out.get("body")
        if out.get("text") and not out.get("body"):
            out["body"] = out.get("text")

        if not out.get("scope"):
            to = str(out.get("to") or "")
            out["scope"] = "public" if to.startswith("@") else "private"

        if not out.get("fromId"):
            out["fromId"] = str(getattr(self.identity, "peer_id", "") or "")
        if not out.get("from_id"):
            out["from_id"] = out["fromId"]

        if not out.get("fromName"):
            out["fromName"] = str(
                getattr(self.identity, "username", "unknown") or "unknown"
            )
        if not out.get("from_name"):
            out["from_name"] = out["fromName"]

        return out

    async def poll_loop(self, path: str, callback: Callable):
        while True:
            try:
                last_ts = self._last_ts_by_path.get(path, 0.0)
                if last_ts > 0:
                    data = self._request(
                        path,
                        params={
                            "orderBy": '"ts"',
                            "startAt": max(0.0, last_ts - 0.001),
                            "limitToLast": 250,
                        },
                    )
                else:
                    data = self._request(
                        path,
                        params={"orderBy": '"ts"', "limitToLast": 250},
                    )
                if isinstance(data, dict):
                    ordered = sorted(
                        data.items(),
                        key=lambda kv: (
                            self._to_seconds((kv[1] or {}).get("ts")),
                            str(kv[0]),
                        ),
                    )
                    for msg_id, payload in ordered:
                        if self._is_seen(path, msg_id):
                            continue
                        msg = self._normalize_msg(path, msg_id, payload)
                        if msg is not None:
                            callback(msg)
                await asyncio.sleep(1.0)
            except Exception:
                await asyncio.sleep(3.0)

    def listen(self, path, callback):
        if path in self._active_tasks:
            return
        task = asyncio.create_task(self.poll_loop(path, callback))
        self._active_tasks[path] = task

    async def fetch_history(self, path: str, limit: int = 50) -> list[dict]:
        data = self._request(path, params={"orderBy": '"$key"', "limitToLast": limit})
        msgs = []
        if isinstance(data, dict):
            for msg_id in sorted(data.keys()):
                msg = self._normalize_msg(path, msg_id, data[msg_id])
                if msg is not None:
                    msgs.append(msg)
        return msgs

    async def fetch_new(self, path: str, limit: int = 100) -> list[dict]:
        last_ts = self._last_ts_by_path.get(path, 0.0)
        if last_ts > 0:
            params = {
                "orderBy": '"ts"',
                "startAt": max(0.0, last_ts - 0.001),
                "limitToLast": max(80, min(limit * 4, 800)),
            }
        else:
            params = {"orderBy": '"ts"', "limitToLast": max(80, min(limit * 4, 800))}
        data = self._request(path, params=params)
        msgs = []
        if isinstance(data, dict):
            ordered = sorted(
                data.items(),
                key=lambda kv: (
                    self._to_seconds((kv[1] or {}).get("ts")),
                    str(kv[0]),
                ),
            )
            for msg_id, payload in ordered:
                if self._is_seen(path, msg_id):
                    continue
                msg = self._normalize_msg(path, msg_id, payload)
                if msg is not None:
                    msgs.append(msg)
        reconciled = self._maybe_reconcile_gap(path, limit=max(200, limit * 2))
        if reconciled:
            msgs.extend(reconciled)
            msgs.sort(key=lambda m: int(m.get("_seq", 0)))
        return msgs

    async def send(self, path, msg) -> bool:
        prepared = self._prepare_outgoing_msg(path, msg)
        res = self._request(path, method="POST", data=prepared)
        return res is not None

    def get_sync_stats(self, path: str) -> dict:
        remote_count = self._count_remote_messages(path)
        seen_count = self._seen_count_by_path.get(path, 0)
        gap = None
        if isinstance(remote_count, int):
            gap = max(0, remote_count - seen_count)
        return {
            "path": path,
            "remote_count": remote_count,
            "seen_count": seen_count,
            "gap": gap,
        }

    def update_presence(
        self,
        ip: str,
        tcp_port: int,
        *,
        udp_port: int | None = None,
        stun_ip: str | None = None,
        stun_port: int | None = None,
    ):
        path = f"presence/{self.identity.peer_id}"
        data = {
            "id": self.identity.peer_id,
            "name": self.identity.username,
            "online": True,
            "public": True,
            "updatedAt": {".sv": "timestamp"},
            "ip": ip,
            "port": tcp_port,
            "udp_port": udp_port,
            "stun_ip": stun_ip,
            "stun_port": stun_port,
        }
        self._request(path, method="PUT", data=data)

    def get_peer_presence(self, peer_id: str) -> dict | None:
        return self._request(f"presence/{peer_id}")

    def get_all_presence(self) -> dict[str, dict] | None:
        data = self._request("presence")
        if isinstance(data, dict):
            return data
        return None

    def resolve_username(self, username: str) -> str | None:
        data = self._request(f"users/{username}")
        if isinstance(data, dict):
            peer_id = data.get("peer_id") or data.get("peerId")
            if peer_id:
                return str(peer_id)
        return None


class DirectPeer:
    def __init__(self, on_message: Callable | None = None):
        self.on_message = on_message
        self.tcp_server: asyncio.Server | None = None
        self.tcp_port: int | None = None
        self.udp_transport: asyncio.DatagramTransport | None = None
        self.udp_port: int | None = None

    async def listen_tcp(self, port: int = 0) -> int:
        if self.tcp_server:
            self.tcp_server.close()
            await self.tcp_server.wait_closed()
            self.tcp_server = None
        self.tcp_server = await asyncio.start_server(
            self._handle_tcp_connection, "0.0.0.0", port
        )
        self.tcp_port = int(self.tcp_server.sockets[0].getsockname()[1])
        return self.tcp_port

    async def _handle_tcp_connection(self, reader, writer):
        from .protocol import read_message

        while True:
            msg = await read_message(reader)
            if msg is None:
                break
            if self.on_message and isinstance(msg, dict):
                self.on_message(msg)

        writer.close()
        await writer.wait_closed()

    async def send_tcp(
        self, ip: str, port: int, msg: dict, timeout_s: float = 3.0
    ) -> bool:
        from .protocol import write_message

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, int(port)), timeout=timeout_s
            )
            ok = await write_message(writer, msg)
            writer.close()
            await writer.wait_closed()
            return ok
        except Exception:
            return False

    async def connect(
        self, peer_id: str, ip: str, port: int, timeout_s: float = 3.0
    ) -> Connection | None:
        probe = {"type": "ping", "from_id": peer_id, "ts": time.time()}
        ok = await self.send_tcp(ip, port, probe, timeout_s=timeout_s)
        if not ok:
            return None
        return Connection(peer_id=peer_id, kind="direct", ip=ip, port=int(port))

    class _UDPProtocol(asyncio.DatagramProtocol):
        def __init__(self, owner: "DirectPeer"):
            self.owner = owner

        def datagram_received(self, data: bytes, addr):
            try:
                payload = json.loads(data.decode("utf-8"))
                if isinstance(payload, dict) and payload.get("kind") == "p2p-probe":
                    resp = {
                        "kind": "p2p-probe-ack",
                        "from_id": self.owner.on_message.__self__.identity.peer_id
                        if hasattr(self.owner.on_message, "__self__")
                        else "unknown",
                    }
                    self.owner.udp_transport.sendto(json.dumps(resp).encode(), addr)
                    return
                if (
                    isinstance(payload, dict)
                    and payload.get("kind") == "p2p-msg"
                    and isinstance(payload.get("msg"), dict)
                ):
                    payload = payload["msg"]
                if self.owner.on_message and isinstance(payload, dict):
                    self.owner.on_message(payload)
            except Exception:
                pass

    async def listen_udp(self, port: int = 0) -> int:
        if self.udp_transport:
            self.udp_transport.close()
            self.udp_transport = None

        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: DirectPeer._UDPProtocol(self), local_addr=("0.0.0.0", int(port))
        )
        self.udp_transport = transport
        self.udp_port = int(transport.get_extra_info("sockname")[1])
        return self.udp_port

    async def send_udp(self, ip: str, port: int, msg: dict) -> bool:
        if not self.udp_transport:
            return False
        try:
            payload = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
            self.udp_transport.sendto(payload, (ip, int(port)))
            return True
        except Exception:
            return False

    async def close_udp(self):
        if self.udp_transport:
            self.udp_transport.close()
            self.udp_transport = None
        self.udp_port = None


class HermesClient:
    def __init__(self, host: str, port: int, peer_id: str):
        self.host = host
        self.port = int(port)
        self.peer_id = peer_id
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
            return self
        except Exception:
            self.reader = None
            self.writer = None
            return None

    async def send(self, msg: dict):
        from .protocol import write_message

        if not self.writer:
            return False
        return await write_message(self.writer, msg)


class HolePuncher:
    def __init__(
        self,
        direct_peer: DirectPeer,
        hermes: HermesClient | None,
        stun_host: str,
        stun_port: int,
        timeout_s: int = 5,
    ):
        self.direct_peer = direct_peer
        self.hermes = hermes
        self.stun_host = stun_host
        self.stun_port = int(stun_port)
        self.timeout_s = int(timeout_s)

    async def _stun_discover(self, local_port: int) -> tuple[str, int] | None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            sock.bind(("0.0.0.0", int(local_port)))
        except OSError:
            sock.close()
            return None

        transaction_id = secrets.token_bytes(12)
        msg_type = 0x0001
        msg_length = 0
        magic_cookie = 0x2112A442
        req = struct.pack(">HHI12s", msg_type, msg_length, magic_cookie, transaction_id)

        loop = asyncio.get_running_loop()
        try:
            await loop.sock_sendto(sock, req, (self.stun_host, self.stun_port))
            data, _ = await asyncio.wait_for(
                loop.sock_recvfrom(sock, 2048), timeout=max(0.5, self.timeout_s / 2)
            )
        except Exception:
            sock.close()
            return None

        sock.close()
        if len(data) < 20:
            return None

        body = data[20:]
        i = 0
        while i + 4 <= len(body):
            attr_type = struct.unpack(">H", body[i : i + 2])[0]
            attr_len = struct.unpack(">H", body[i + 2 : i + 4])[0]
            val_start = i + 4
            val_end = val_start + attr_len
            if val_end > len(body):
                break
            value = body[val_start:val_end]

            if attr_type == 0x0020 and attr_len >= 8:
                family = value[1]
                if family == 0x01:
                    xport = struct.unpack(">H", value[2:4])[0]
                    port = xport ^ (magic_cookie >> 16)
                    cookie_bytes = struct.pack(">I", magic_cookie)
                    ip_raw = bytes([value[4 + j] ^ cookie_bytes[j] for j in range(4)])
                    return socket.inet_ntoa(ip_raw), int(port)

            if attr_type == 0x0001 and attr_len >= 8:
                family = value[1]
                if family == 0x01:
                    port = struct.unpack(">H", value[2:4])[0]
                    return socket.inet_ntoa(value[4:8]), int(port)

            i = val_end + ((4 - (attr_len % 4)) % 4)

        return None

    async def discover_public_endpoint(self) -> tuple[str, int] | None:
        if self.direct_peer.udp_port is None:
            return None
        return await self._stun_discover(self.direct_peer.udp_port)

    async def punch(
        self, target_peer_id: str, target_ip: str, target_udp_port: int
    ) -> Connection | None:
        if not self.direct_peer.udp_port:
            return None
        probe = {
            "kind": "p2p-probe",
            "from_id": getattr(self.hermes, "peer_id", "unknown"),
            "ts": time.time(),
        }
        for _ in range(5):
            await self.direct_peer.send_udp(target_ip, int(target_udp_port), probe)
            await asyncio.sleep(0.1)

        return Connection(
            peer_id=target_peer_id, kind="udp", ip=target_ip, port=int(target_udp_port)
        )


class TransportManager:
    def __init__(self, config, identity):
        self.config = config
        self.identity = identity
        self.fb = FirebaseTransport(config, identity)
        self._on_message = None

        self.direct_peer = DirectPeer()
        self.hermes = HermesClient(
            getattr(config, "hermes_host", "127.0.0.1"),
            getattr(config, "hermes_port", 7777),
            getattr(identity, "peer_id", "unknown"),
        )
        self.holepuncher = HolePuncher(
            self.direct_peer,
            self.hermes,
            getattr(config, "stun_host", "stun.l.google.com"),
            int(getattr(config, "stun_port", 19302)),
            int(getattr(config, "holepunch_timeout_s", 5)),
        )

        self.status = {
            "connected": True,
            "ip": None,
            "port": None,
            "direct_port": None,
            "udp_port": None,
            "stun_ip": None,
            "stun_port": None,
            "last_transport": None,
            "hermes_connected": False,
        }
        self._inbox_path: str | None = None

    def set_on_message(self, callback):
        self._on_message = callback
        self.direct_peer.on_message = callback

    def resolve_direct_target(self, target: str) -> str:
        raw = str(target or "").strip()
        if not raw or raw.startswith("@") or raw == "*":
            return raw
        if self.fb.get_peer_presence(raw):
            return raw
        resolved = self.fb.resolve_username(raw)
        return resolved or raw

    async def initialize(self, listen_port: int | None = None) -> bool:
        self.status["direct_port"] = await self.direct_peer.listen_tcp(listen_port or 0)
        self.status["port"] = self.status["direct_port"]
        self.status["udp_port"] = await self.direct_peer.listen_udp(0)

        public_ep = await self.holepuncher.discover_public_endpoint()
        if public_ep:
            self.status["stun_ip"], self.status["stun_port"] = public_ep
            logger.info(
                f"Discovered public endpoint: {self.status['stun_ip']}:{self.status['stun_port']}"
            )

        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
                self.status["ip"] = resp.read().decode("utf-8")
        except Exception:
            self.status["ip"] = self.status["stun_ip"] or "127.0.0.1"

        try:
            await self.hermes.connect()
            self.status["hermes_connected"] = bool(self.hermes.writer)
        except Exception:
            self.status["hermes_connected"] = False

        return True

    def update_presence(self):
        if not self.status.get("ip") or not self.status.get("direct_port"):
            return

        self.fb.update_presence(
            self.status["ip"],
            int(self.status["direct_port"]),
            udp_port=self.status.get("udp_port"),
            stun_ip=self.status.get("stun_ip"),
            stun_port=self.status.get("stun_port"),
        )

    async def authenticate(self, u, p):
        return await self.fb.authenticate(u, p)

    async def join_channel(self, name, password=None) -> dict:
        res = await self.fb.join_room(name, password)
        if res.get("ok"):
            self.fb.listen(res["path"], self._on_message)
        return res

    async def create_channel(self, name: str, password: str | None = None) -> dict:
        return await self.join_channel(name, password)

    async def delete_channel(self, name: str) -> dict:
        ok = await self.fb.delete_room(name)
        return {"ok": bool(ok), "channel": name}

    async def list_channels(self) -> list[str]:
        return await self.fb.list_rooms()

    async def rename_channel(self, old_name: str, new_name: str) -> dict:
        ok = await self.fb.rename_room(old_name, new_name)
        return {"ok": bool(ok), "old": old_name, "new": new_name}

    async def start_personal_inbox_listener(self):
        if not getattr(self.identity, "peer_id", None):
            return
        self._inbox_path = f"messages/{self.identity.peer_id}"
        self.fb.listen(self._inbox_path, self._on_message)

    async def load_new_messages(self, channel, limit=100):
        path = self._path_for_target(channel)
        history = await self.fb.fetch_new(path, limit=limit)
        for m in history:
            if self._on_message:
                self._on_message(m)
        return len(history)

    async def load_more(self, channel, limit=100):
        path = self._path_for_target(channel)
        history = await self.fb.fetch_history(path, limit=limit)
        for m in history:
            if self._on_message:
                self._on_message(m)
        return len(history)

    def get_sync_status(self, channel: str) -> dict:
        path = self._path_for_target(channel)
        return self.fb.get_sync_stats(path)

    def _path_for_target(self, target: str) -> str:
        raw = str(target or "")
        if raw in {"*", "broadcast", "@broadcast"}:
            return "messages/broadcast"
        if raw.startswith("@"):
            raw = "@" + raw[1:].lower()
        safe_target = raw.replace("@", "chan_").replace("*", "broadcast")
        return f"messages/{safe_target}"

    def list_online_peers(self) -> list[dict]:
        data = self.fb.get_all_presence() or {}
        peers = []
        for _, item in data.items():
            if not isinstance(item, dict) or not item.get("online"):
                continue
            pid = str(item.get("id") or "")
            if not pid or pid == getattr(self.identity, "peer_id", None):
                continue
            peers.append(
                {
                    "id": pid,
                    "name": item.get("name") or pid,
                    "ip": item.get("ip"),
                    "port": item.get("port"),
                    "udp_port": item.get("udp_port"),
                    "stun_ip": item.get("stun_ip"),
                    "stun_port": item.get("stun_port"),
                }
            )
        return peers

    async def _send_via_firebase(self, target_id: str, msg: dict) -> bool:
        path = self._path_for_target(target_id)
        ok = await self.fb.send(path, msg)
        if ok:
            self.status["last_transport"] = "firebase"
        return ok

    async def _send_direct_tcp(self, presence: dict, msg: dict) -> bool:
        ip = presence.get("ip")
        port = presence.get("port")
        if not ip or not port:
            return False
        ok = await self.direct_peer.send_tcp(str(ip), int(port), msg)
        if ok:
            self.status["last_transport"] = "direct_tcp"
        return ok

    async def _send_direct_udp(self, target_id: str, presence: dict, msg: dict) -> bool:
        ip = presence.get("stun_ip") or presence.get("ip")
        port = presence.get("stun_port") or presence.get("udp_port")
        if not ip or not port:
            return False

        punch_req = {
            "type": "p2p-punch-request",
            "from_id": self.identity.peer_id,
            "ip": self.status["stun_ip"],
            "port": self.status["stun_port"],
        }
        await self._send_via_firebase(target_id, punch_req)

        conn = await self.holepuncher.punch(target_id, str(ip), int(port))
        if not conn:
            return False

        wrapped = {"kind": "p2p-msg", "msg": msg}
        ok = await self.direct_peer.send_udp(str(ip), int(port), wrapped)
        if ok:
            self.status["last_transport"] = "udp_holepunch"
        return ok

    async def send(self, target_id, msg) -> bool:
        if (
            not isinstance(target_id, str)
            or target_id.startswith("@")
            or target_id == "*"
        ):
            return await self._send_via_firebase(str(target_id), msg)

        resolved_target = self.resolve_direct_target(target_id)
        payload = dict(msg or {})
        payload["to"] = resolved_target

        presence = self.fb.get_peer_presence(resolved_target)
        if isinstance(presence, dict):
            if await self._send_direct_tcp(presence, payload):
                return True
            if await self._send_direct_udp(resolved_target, presence, payload):
                return True
        return await self._send_via_firebase(resolved_target, payload)

    async def broadcast(self, msg) -> bool:
        return await self._send_via_firebase("@broadcast", msg)

    async def set_listen_port(self, port: int) -> int:
        bound = await self.direct_peer.listen_tcp(int(port))
        self.status["direct_port"] = int(bound)
        self.status["port"] = int(bound)
        self.update_presence()
        return int(bound)

    async def create_random_listen_port(self) -> int:
        return await self.set_listen_port(0)

    async def ping_peer(self, peer_id: str) -> dict:
        resolved = self.resolve_direct_target(peer_id)
        presence = self.fb.get_peer_presence(resolved)
        if not isinstance(presence, dict):
            return {
                "ok": False,
                "error": "peer offline or unknown",
                "peer_id": resolved,
            }
        host = presence.get("ip")
        port = presence.get("port")
        if not host or not port:
            return {
                "ok": False,
                "error": "peer has no direct endpoint",
                "peer_id": resolved,
            }
        return await self.ping_host(str(host), int(port), peer_id=resolved)

    async def ping_host(self, host: str, port: int, peer_id: str | None = None) -> dict:
        started = time.time()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)), timeout=3.0
            )
            writer.close()
            await writer.wait_closed()
            return {
                "ok": True,
                "peer_id": peer_id,
                "host": host,
                "port": int(port),
                "latency_ms": int((time.time() - started) * 1000),
            }
        except Exception as e:
            return {
                "ok": False,
                "peer_id": peer_id,
                "host": host,
                "port": int(port),
                "error": str(e),
            }

    async def resolve_host(self, host: str) -> dict:
        started = time.time()
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, None, proto=socket.IPPROTO_TCP
            )
            addrs = []
            for info in infos:
                ip = info[4][0]
                if ip not in addrs:
                    addrs.append(ip)
            return {
                "ok": True,
                "host": host,
                "addresses": addrs,
                "latency_ms": int((time.time() - started) * 1000),
            }
        except Exception as e:
            return {"ok": False, "host": host, "error": str(e)}

    async def scan_common_ports(self, host: str) -> dict:
        started = time.time()
        common = [22, 53, 80, 123, 443, 3000, 5000, 5432, 6379, 7777, 8080]
        open_ports = []
        checked = 0
        for port in common:
            checked += 1
            res = await self.ping_host(host, port)
            if res.get("ok"):
                open_ports.append(
                    {"port": int(port), "latency_ms": res.get("latency_ms", 0)}
                )
        return {
            "ok": True,
            "host": host,
            "checked": checked,
            "open_ports": open_ports,
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    async def list_lan_devices(self) -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                "arp",
                "-a",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=4.0)
            text = out.decode("utf-8", errors="ignore")
            devices = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = re.search(r"^(.+?) \(([^)]+)\) at ([^ ]+)", line)
                if not m:
                    continue
                host, ip, mac = m.group(1), m.group(2), m.group(3)
                devices.append({"host": host, "ip": ip, "mac": mac})
            return {"ok": True, "count": len(devices), "devices": devices}
        except Exception as e:
            return {"ok": False, "error": str(e), "devices": []}

    async def send_raw(self, host: str, port: int, text: str) -> bool:
        payload = {
            "type": "msg",
            "id": str(uuid4()),
            "from_id": self.identity.peer_id,
            "from_name": self.identity.username,
            "to": f"{host}:{int(port)}",
            "body": str(text),
            "enc": "none",
            "ts": time.time(),
        }
        return await self.direct_peer.send_tcp(str(host), int(port), payload)

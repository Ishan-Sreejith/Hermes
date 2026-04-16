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

    def _request(self, path: str, method: str = "GET", data: Any = None, params: dict | None = None) -> Any:
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
        res = self._request(f"users/{username}", method="PUT", data={"password": hashed, "peer_id": peer_id})
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
            self._request(f"rooms/{safe_name}", method="PUT", data={"password": hashed, "created_at": time.time()})

        path = f"messages/chan_{safe_name}" if safe_name != "broadcast" else "messages/broadcast"
        return {"ok": True, "path": path}

    def _mark_seen(self, path: str, key: str):
        self._seen_ids.add(f"{path}:{key}")
        self._last_key_by_path[path] = key

    def _is_seen(self, path: str, key: str) -> bool:
        return f"{path}:{key}" in self._seen_ids

    def _normalize_msg(self, path: str, msg_key: str, raw_msg: Any) -> dict | None:
        if not isinstance(raw_msg, dict):
            return None
        msg = dict(raw_msg)
        if not msg.get("id"):
            msg["id"] = msg_key
        msg["_firebase_key"] = msg_key
        self._mark_seen(path, msg_key)
        return msg

    async def poll_loop(self, path: str, callback: Callable):
        while True:
            try:
                cursor = self._last_key_by_path.get(path)
                if cursor:
                    data = self._request(
                        path,
                        params={"orderBy": '"$key"', "startAt": f'"{cursor}"', "limitToFirst": 100},
                    )
                else:
                    data = self._request(path, params={"orderBy": '"$key"', "limitToLast": 20})
                if isinstance(data, dict):
                    for msg_id in sorted(data.keys()):
                        if self._is_seen(path, msg_id):
                            continue
                        msg = self._normalize_msg(path, msg_id, data[msg_id])
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
        cursor = self._last_key_by_path.get(path)
        if cursor:
            params = {"orderBy": '"$key"', "startAt": f'"{cursor}"', "limitToFirst": limit}
        else:
            params = {"orderBy": '"$key"', "limitToLast": max(20, min(limit, 100))}
        data = self._request(path, params=params)
        msgs = []
        if isinstance(data, dict):
            for msg_id in sorted(data.keys()):
                if self._is_seen(path, msg_id):
                    continue
                msg = self._normalize_msg(path, msg_id, data[msg_id])
                if msg is not None:
                    msgs.append(msg)
        return msgs

    async def send(self, path, msg) -> bool:
        res = self._request(path, method="POST", data=msg)
        return res is not None

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
        self.tcp_server = await asyncio.start_server(self._handle_tcp_connection, "0.0.0.0", port)
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

    async def send_tcp(self, ip: str, port: int, msg: dict, timeout_s: float = 3.0) -> bool:
        from .protocol import write_message

        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, int(port)), timeout=timeout_s)
            ok = await write_message(writer, msg)
            writer.close()
            await writer.wait_closed()
            return ok
        except Exception:
            return False

    async def connect(self, peer_id: str, ip: str, port: int, timeout_s: float = 3.0) -> Connection | None:
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
                    return
                if isinstance(payload, dict) and payload.get("kind") == "p2p-msg" and isinstance(payload.get("msg"), dict):
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
            payload = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
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
            data, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), timeout=max(0.2, self.timeout_s))
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

    async def punch(self, target_peer_id: str, target_ip: str, target_udp_port: int) -> Connection | None:
        if not self.direct_peer.udp_port:
            return None

        my_public = await self._stun_discover(self.direct_peer.udp_port)
        if not my_public:
            return None

        probe = {
            "kind": "p2p-probe",
            "from_id": getattr(self.hermes, "peer_id", "unknown"),
            "ts": time.time(),
            "public_ip": my_public[0],
            "public_port": my_public[1],
        }
        for _ in range(4):
            await self.direct_peer.send_udp(target_ip, int(target_udp_port), probe)
            await asyncio.sleep(0.08)

        return Connection(peer_id=target_peer_id, kind="udp", ip=target_ip, port=int(target_udp_port))


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
            "last_transport": None,
            "hermes_connected": False,
        }
        self._inbox_path: str | None = None

    def set_on_message(self, callback):
        self._on_message = callback
        self.direct_peer.on_message = callback

    async def initialize(self, listen_port: int | None = None) -> bool:
        self.status["direct_port"] = await self.direct_peer.listen_tcp(listen_port or 0)
        self.status["port"] = self.status["direct_port"]
        self.status["udp_port"] = await self.direct_peer.listen_udp(0)

        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
                self.status["ip"] = resp.read().decode("utf-8")
        except Exception:
            self.status["ip"] = "127.0.0.1"

        try:
            await self.hermes.connect()
            self.status["hermes_connected"] = bool(self.hermes.writer)
        except Exception:
            self.status["hermes_connected"] = False

        return True

    def update_presence(self):
        if not self.status.get("ip") or not self.status.get("direct_port"):
            return

        stun_ep = None
        self.fb.update_presence(
            self.status["ip"],
            int(self.status["direct_port"]),
            udp_port=self.status.get("udp_port"),
            stun_ip=(stun_ep[0] if stun_ep else None),
            stun_port=(stun_ep[1] if stun_ep else None),
        )

    async def authenticate(self, u, p):
        return await self.fb.authenticate(u, p)

    async def join_channel(self, name, password=None) -> dict:
        res = await self.fb.join_room(name, password)
        if res.get("ok"):
            history = await self.fb.fetch_history(res["path"], limit=10)
            for m in history:
                if self._on_message:
                    self._on_message(m)
            self.fb.listen(res["path"], self._on_message)
        return res

    async def start_personal_inbox_listener(self):
        if not getattr(self.identity, "peer_id", None):
            return
        self._inbox_path = f"messages/{self.identity.peer_id}"
        history = await self.fb.fetch_history(self._inbox_path, limit=20)
        for m in history:
            if self._on_message:
                self._on_message(m)
        self.fb.listen(self._inbox_path, self._on_message)

    async def load_more(self, channel, limit=50):
        safe_target = channel.replace("@", "chan_").replace("*", "broadcast")
        if safe_target == "broadcast":
            safe_target = "broadcast"
        path = f"messages/{safe_target}"
        history = await self.fb.fetch_history(path, limit=limit)
        for m in history:
            if self._on_message:
                self._on_message(m)

    async def load_new_messages(self, channel, limit=100):
        safe_target = channel.replace("@", "chan_").replace("*", "broadcast")
        if safe_target == "broadcast":
            safe_target = "broadcast"
        path = f"messages/{safe_target}"
        history = await self.fb.fetch_new(path, limit=limit)
        for m in history:
            if self._on_message:
                self._on_message(m)
        return len(history)

    async def set_listen_port(self, port: int) -> int:
        self.status["direct_port"] = await self.direct_peer.listen_tcp(port)
        self.status["port"] = self.status["direct_port"]
        self.update_presence()
        return int(self.status["direct_port"])

    async def create_random_listen_port(self) -> int:
        return await self.set_listen_port(0)

    def list_online_peers(self) -> list[dict]:
        data = self.fb.get_all_presence() or {}
        peers = []
        for _, item in data.items():
            if not isinstance(item, dict):
                continue
            if not item.get("online"):
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
                }
            )
        peers.sort(key=lambda p: str(p.get("name")))
        return peers

    async def ping_host(self, host: str, port: int, timeout_s: float = 2.0) -> dict:
        started = time.perf_counter()
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout=timeout_s)
            writer.close()
            await writer.wait_closed()
            elapsed = int((time.perf_counter() - started) * 1000)
            return {"ok": True, "host": host, "port": int(port), "latency_ms": elapsed}
        except Exception as e:
            elapsed = int((time.perf_counter() - started) * 1000)
            return {"ok": False, "host": host, "port": int(port), "latency_ms": elapsed, "error": str(e)}

    async def ping_peer(self, peer_id: str) -> dict:
        presence = self.fb.get_peer_presence(peer_id)
        if not isinstance(presence, dict):
            return {"ok": False, "peer_id": peer_id, "error": "peer not found in presence"}
        ip = presence.get("ip")
        port = presence.get("port")
        if not ip or not port:
            return {"ok": False, "peer_id": peer_id, "error": "peer has no reachable ip/port"}
        result = await self.ping_host(str(ip), int(port))
        result["peer_id"] = peer_id
        return result

    async def resolve_host(self, host: str) -> dict:
        loop = asyncio.get_running_loop()
        started = time.perf_counter()
        try:
            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            addrs = sorted({item[4][0] for item in infos if item and item[4]})
            elapsed = int((time.perf_counter() - started) * 1000)
            if not addrs:
                return {"ok": False, "host": host, "latency_ms": elapsed, "error": "no address found"}
            return {"ok": True, "host": host, "latency_ms": elapsed, "addresses": addrs}
        except Exception as e:
            elapsed = int((time.perf_counter() - started) * 1000)
            return {"ok": False, "host": host, "latency_ms": elapsed, "error": str(e)}

    async def scan_common_ports(self, host: str, ports: list[int] | None = None, timeout_s: float = 0.45) -> dict:
        if not ports:
            ports = [22, 53, 80, 123, 135, 139, 443, 445, 3306, 3389, 5432, 6379, 8080]

        async def _probe(p: int):
            res = await self.ping_host(host, int(p), timeout_s=timeout_s)
            return int(p), bool(res.get("ok")), int(res.get("latency_ms", 0))

        started = time.perf_counter()
        results = await asyncio.gather(*[_probe(p) for p in ports], return_exceptions=True)
        open_ports: list[dict] = []
        failed = 0
        for item in results:
            if isinstance(item, Exception):
                failed += 1
                continue
            port, ok, latency = item
            if ok:
                open_ports.append({"port": port, "latency_ms": latency})
        elapsed = int((time.perf_counter() - started) * 1000)
        open_ports.sort(key=lambda p: p["port"])
        return {
            "ok": True,
            "host": host,
            "elapsed_ms": elapsed,
            "checked": len(ports),
            "failed_probes": failed,
            "open_ports": open_ports,
        }

    async def list_lan_devices(self) -> dict:
        def _run_arp() -> str:
            out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=8)
            if out.returncode != 0 and out.stderr:
                raise RuntimeError(out.stderr.strip())
            return out.stdout or ""

        try:
            output = await asyncio.to_thread(_run_arp)
        except Exception as e:
            return {"ok": False, "error": str(e), "devices": []}

        devices = []
        for line in output.splitlines():
            match = re.search(r"\(([\d.]+)\)\s+at\s+([0-9a-fA-F:.-]+)", line)
            if not match:
                continue
            ip = match.group(1)
            mac = match.group(2)
            hostname = line.split("(", 1)[0].strip() or "unknown"
            devices.append({"host": hostname, "ip": ip, "mac": mac})

        devices.sort(key=lambda d: d["ip"])
        return {"ok": True, "count": len(devices), "devices": devices}

    async def _send_via_firebase(self, target_id: str, msg: dict) -> bool:
        safe_target = target_id.replace("@", "chan_").replace("*", "broadcast")
        if safe_target == "broadcast":
            safe_target = "broadcast"
        path = f"messages/{safe_target}"
        ok = await self.fb.send(path, msg)
        if ok:
            self.status["last_transport"] = "firebase"
        return ok

    async def _send_direct_via_firebase(self, target_id: str, msg: dict) -> bool:
        target_path = f"messages/{target_id}"
        ok = await self.fb.send(target_path, msg)

        self_id = getattr(self.identity, "peer_id", None)
        if self_id and self_id != target_id:
            await self.fb.send(f"messages/{self_id}", msg)

        if ok:
            self.status["last_transport"] = "firebase"
        return ok

    async def _send_direct_tcp(self, presence: dict, msg: dict) -> bool:
        ip = presence.get("ip")
        port = presence.get("port")
        if not ip or not port:
            return False
        ok = await self.direct_peer.send_tcp(str(ip), int(port), msg, timeout_s=float(getattr(self.config, "direct_timeout_s", 3)))
        if ok:
            self.status["last_transport"] = "direct_tcp"
        return ok

    async def _send_direct_udp(self, target_id: str, presence: dict, msg: dict) -> bool:
        udp_port = presence.get("udp_port") or presence.get("stun_port")
        ip = presence.get("stun_ip") or presence.get("ip")
        if not ip or not udp_port:
            return False

        conn = await self.holepuncher.punch(target_id, str(ip), int(udp_port))
        if not conn:
            return False

        wrapped = {"kind": "p2p-msg", "msg": msg}
        ok = await self.direct_peer.send_udp(conn.ip or str(ip), int(conn.port or udp_port), wrapped)
        if ok:
            self.status["last_transport"] = "udp_holepunch"
        return ok

    async def connect(self, target_id: str) -> Connection:
        presence = self.fb.get_peer_presence(target_id)
        if isinstance(presence, dict):
            if await self._send_direct_tcp(presence, {"type": "ping", "from_id": self.identity.peer_id, "ts": time.time()}):
                return Connection(peer_id=target_id, kind="direct", ip=presence.get("ip"), port=presence.get("port"))
            if await self._send_direct_udp(target_id, presence, {"type": "ping", "from_id": self.identity.peer_id, "ts": time.time()}):
                return Connection(peer_id=target_id, kind="udp", ip=presence.get("ip"), port=presence.get("udp_port"))
        self.status["last_transport"] = "relay"
        return Connection(peer_id=target_id, kind="relay")

    async def send(self, target_id, msg) -> bool:
        if not isinstance(target_id, str) or target_id.startswith("@") or target_id == "*":
            return await self._send_via_firebase(str(target_id), msg)

        presence = self.fb.get_peer_presence(target_id)
        if isinstance(presence, dict):
            if await self._send_direct_tcp(presence, msg):
                return True
            if await self._send_direct_udp(target_id, presence, msg):
                return True

        return await self._send_direct_via_firebase(target_id, msg)

    async def broadcast(self, msg) -> bool:
        ok = await self.fb.send("messages/broadcast", msg)
        if ok:
            self.status["last_transport"] = "firebase"
        return ok

    async def send_raw(self, host: str, port: int, text: str) -> bool:
        try:
            _, writer = await asyncio.open_connection(host, int(port))
            writer.write((text + "\n").encode("utf-8"))
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

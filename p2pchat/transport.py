from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from .protocol import read_message, write_message


@dataclass
class Connection:
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    peer_id: str | None = None
    kind: str = "direct"
    udp_socket: socket.socket | None = None
    udp_peer: tuple[str, int] | None = None


class CloudQueueClient:
    """Abstract cloud queue transport used for Firebase-style fallback."""

    async def enqueue(self, msg: dict[str, Any]) -> bool:
        return False

    async def poll(self, on_message: Callable[[dict], None]) -> int:
        return 0

    async def delete(self, msg_id: str) -> bool:
        return False


class FirebaseCloudQueueClient(CloudQueueClient):
    """Firebase Realtime Database queue backend.

    The implementation prefers the Firebase Admin SDK when available, but also
    supports a REST fallback using a service account JSON or anonymous database
    URL for local testing. Queue messages are stored under a per-recipient path
    and deleted once successfully delivered.
    """

    def __init__(self, config: Any, identity: Any):
        self.config = config
        self.identity = identity
        self.enabled = bool(getattr(config.cloud, "enabled", False))
        self.backend = getattr(config.cloud, "backend", "firebase")
        self.queue_path = getattr(config.cloud, "queue_path", "messages")
        self.project_id = getattr(config.cloud, "project_id", None)
        self.database_url = getattr(config.cloud, "database_url", None)
        self.credentials_path = getattr(config.cloud, "credentials_path", None)
        self.service_account_json = getattr(config.cloud, "service_account_json", None)
        self.delivery_ttl_s = int(getattr(config.cloud, "delivery_ttl_s", 300))
        self._firebase_app = None
        self._db = None
        self._init_error: Exception | None = None
        self._rest_token: str | None = None
        self._rest_token_expiry: float = 0.0
        if self.enabled and self.backend == "firebase":
            self._initialize_backend()

    def _initialize_backend(self) -> None:
        try:
            import firebase_admin  # type: ignore
            from firebase_admin import credentials, db  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency path
            self._init_error = exc
            return

        try:
            if firebase_admin._apps:  # type: ignore[attr-defined]
                self._firebase_app = firebase_admin.get_app()
            else:
                cred = None
                cred_path = self.credentials_path or self.service_account_json
                if cred_path:
                    cred = credentials.Certificate(cred_path)
                elif self.project_id:
                    cred = credentials.ApplicationDefault()
                if self.database_url:
                    self._firebase_app = firebase_admin.initialize_app(cred, {"databaseURL": self.database_url})
                else:
                    self._firebase_app = firebase_admin.initialize_app(cred)
            self._db = db
        except Exception as exc:  # pragma: no cover - optional dependency path
            self._init_error = exc

    def _queue_root(self) -> str:
        return self.queue_path.strip("/") or "messages"

    def _recipient_path(self, peer_id: str) -> str:
        return f"{self._queue_root()}/{peer_id}"

    def _message_payload(self, msg: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": msg.get("id") or str(uuid4()),
            "type": msg.get("type", "msg"),
            "from_id": msg.get("from_id"),
            "from_name": msg.get("from_name"),
            "to": msg.get("to"),
            "channel": msg.get("channel"),
            "ts": float(msg.get("ts") or time.time()),
            "enc": msg.get("enc", "none"),
            "body": msg.get("body", ""),
            "delivered": False,
            "claimed_by": None,
            "claimed_at": None,
            "expires_at": float(msg.get("expires_at") or (time.time() + self.delivery_ttl_s)),
        }

    async def _rest_authorized_headers(self) -> dict[str, str]:
        if self._rest_token and time.time() < self._rest_token_expiry - 60:
            return {"Authorization": f"Bearer {self._rest_token}"}
        if not self.service_account_json:
            return {}
        try:
            import google.auth.transport.requests  # type: ignore
            from google.oauth2 import service_account  # type: ignore
        except Exception:
            return {}
        creds = service_account.Credentials.from_service_account_file(
            self.service_account_json,
            scopes=["https://www.googleapis.com/auth/firebase.database", "https://www.googleapis.com/auth/userinfo.email"],
        )
        request = google.auth.transport.requests.Request()
        creds.refresh(request)
        self._rest_token = creds.token
        self._rest_token_expiry = float(getattr(creds, "expiry", None).timestamp() if getattr(creds, "expiry", None) else time.time() + 3000)
        return {"Authorization": f"Bearer {self._rest_token}"}

    def _db_url(self) -> str | None:
        if self.database_url:
            return self.database_url.rstrip("/")
        if self.project_id:
            return f"https://{self.project_id}-default-rtdb.firebaseio.com"
        return None

    async def enqueue(self, msg: dict[str, Any]) -> bool:
        if not self.enabled or self.backend != "firebase":
            return False
        payload = self._message_payload(msg)
        target = str(payload.get("to") or payload.get("channel") or "*")
        recipient = target if target != "*" else "broadcast"
        if self._db is not None and self._firebase_app is not None:
            try:
                ref = self._db.reference(self._recipient_path(recipient), app=self._firebase_app)
                ref.child(payload["id"]).set(payload)
                return True
            except Exception as exc:
                self._init_error = exc
                return False
        return await self._enqueue_rest(recipient, payload)

    async def _enqueue_rest(self, recipient: str, payload: dict[str, Any]) -> bool:
        import urllib.request

        base = self._db_url()
        if not base:
            return False
        url = f"{base}/{self._recipient_path(recipient)}.json"
        headers = {"Content-Type": "application/json"}
        headers.update(await self._rest_authorized_headers())
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="PATCH")
        try:
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception as exc:
            self._init_error = exc
            return False

    async def poll(self, on_message: Callable[[dict], None]) -> int:
        if not self.enabled or self.backend != "firebase":
            return 0
        if self._db is not None and self._firebase_app is not None:
            return await self._poll_admin(on_message)
        return await self._poll_rest(on_message)

    async def _poll_admin(self, on_message: Callable[[dict], None]) -> int:
        delivered = 0
        try:
            recipient = self._recipient_path(self.identity.peer_id)
            ref = self._db.reference(recipient, app=self._firebase_app)
            payloads = ref.get() or {}
            now = time.time()
            for msg_id, payload in list(payloads.items()):
                if not isinstance(payload, dict):
                    continue
                expires_at = float(payload.get("expires_at") or 0)
                if expires_at and expires_at < now:
                    ref.child(msg_id).delete()
                    continue
                if payload.get("claimed_by") and payload.get("claimed_by") != self.identity.peer_id:
                    continue
                payload["claimed_by"] = self.identity.peer_id
                payload["claimed_at"] = now
                ref.child(msg_id).update({"claimed_by": self.identity.peer_id, "claimed_at": now})
                on_message(payload)
                ref.child(msg_id).delete()
                delivered += 1
        except Exception as exc:
            self._init_error = exc
        return delivered

    async def _poll_rest(self, on_message: Callable[[dict], None]) -> int:
        import urllib.request

        base = self._db_url()
        if not base:
            return 0
        recipient = self._recipient_path(self.identity.peer_id)
        url = f"{base}/{recipient}.json"
        headers = await self._rest_authorized_headers()
        req = urllib.request.Request(url, headers=headers, method="GET")
        delivered = 0
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
            now = time.time()
            for msg_id, payload in list((data or {}).items()):
                if not isinstance(payload, dict):
                    continue
                expires_at = float(payload.get("expires_at") or 0)
                if expires_at and expires_at < now:
                    await self.delete(msg_id)
                    continue
                payload["claimed_by"] = self.identity.peer_id
                payload["claimed_at"] = now
                on_message(payload)
                await self.delete(msg_id)
                delivered += 1
        except Exception as exc:
            self._init_error = exc
        return delivered

    async def delete(self, msg_id: str) -> bool:
        if not self.enabled or self.backend != "firebase":
            return False
        if self._db is not None and self._firebase_app is not None:
            try:
                ref = self._db.reference(self._recipient_path(self.identity.peer_id), app=self._firebase_app)
                ref.child(msg_id).delete()
                return True
            except Exception as exc:
                self._init_error = exc
                return False
        return await self._delete_rest(msg_id)

    async def _delete_rest(self, msg_id: str) -> bool:
        import urllib.request

        base = self._db_url()
        if not base:
            return False
        url = f"{base}/{self._recipient_path(self.identity.peer_id)}/{msg_id}.json"
        headers = await self._rest_authorized_headers()
        req = urllib.request.Request(url, headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception as exc:
            self._init_error = exc
            return False


class DirectPeer:
    """TCP peer connection."""

    def __init__(self, listen_port: int = 0):
        self.listen_port = listen_port
        self.server: asyncio.base_events.Server | None = None
        self.on_message: Callable[[dict], None] | None = None
        self.udp_socket: socket.socket | None = None
        self.udp_task: asyncio.Task | None = None
        self.udp_port: int | None = None

    async def connect(self, host: str, port: int, timeout: float = 3.0) -> Connection | None:
        """Connect to a peer via TCP."""
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
            return Connection(reader=reader, writer=writer, kind="direct")
        except (asyncio.TimeoutError, OSError):
            return None

    async def listen(self, port: int = 0) -> int:
        """Start listening for incoming connections."""

        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    msg = await read_message(reader)
                    if msg is None:
                        break
                    if self.on_message:
                        self.on_message(msg)
            finally:
                writer.close()
                await writer.wait_closed()

        self.server = await asyncio.start_server(handle_client, "0.0.0.0", port)
        addr = self.server.sockets[0].getsockname()
        self.listen_port = int(addr[1])
        return self.listen_port

    async def listen_udp(self, port: int = 0) -> int:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        sock.setblocking(False)
        self.udp_socket = sock
        self.udp_port = sock.getsockname()[1]
        self.udp_task = loop.create_task(self._udp_receive_loop())
        return self.udp_port

    async def _udp_receive_loop(self) -> None:
        assert self.udp_socket is not None
        loop = asyncio.get_running_loop()
        while self.udp_socket:
            try:
                data, addr = await loop.sock_recvfrom(self.udp_socket, 65535)
            except (OSError, asyncio.CancelledError):
                break
            if not data:
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if msg.get("type") == "holepunch_ack":
                continue
            msg["_udp_addr"] = addr
            if self.on_message:
                self.on_message(msg)

    async def udp_send(self, payload: dict[str, Any], host: str, port: int) -> bool:
        if not self.udp_socket:
            return False
        loop = asyncio.get_running_loop()
        try:
            await loop.sock_sendto(self.udp_socket, json.dumps(payload).encode("utf-8"), (host, port))
            return True
        except OSError:
            return False

    async def close_udp(self) -> None:
        if self.udp_task:
            self.udp_task.cancel()
            with contextlib.suppress(Exception):
                await self.udp_task
            self.udp_task = None
        if self.udp_socket:
            self.udp_socket.close()
            self.udp_socket = None


class HolePuncher:
    def __init__(self, direct_peer: DirectPeer, hermes: "HermesClient", stun_host: str, stun_port: int, timeout_s: int = 5):
        self.direct_peer = direct_peer
        self.hermes = hermes
        self.stun_host = stun_host
        self.stun_port = stun_port
        self.timeout_s = timeout_s

    async def _stun_discover(self, udp_port: int) -> tuple[str, int] | None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", udp_port))
        sock.setblocking(False)
        try:
            req = b"\x00\x01" + b"\x00" * 18
            await loop.sock_sendto(sock, req, (self.stun_host, self.stun_port))
            try:
                data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), timeout=self.timeout_s)
                if data:
                    return addr[0], int(addr[1])
            except asyncio.TimeoutError:
                return None
        finally:
            sock.close()
        return None

    async def punch(self, peer_id: str, rendezvous_host: str | None, rendezvous_port: int | None, signal_payload: dict[str, Any] | None = None) -> Connection | None:
        await self.direct_peer.listen_udp(0)
        local_port = self.direct_peer.udp_port or 0
        local_public = await self._stun_discover(local_port)
        if not local_public:
            return None
        public_host, public_port = local_public
        await self.hermes.send({
            "type": "relay",
            "to": peer_id,
            "body": {
                "type": "holepunch_signal",
                "from_id": self.hermes.peer_id,
                "peer_id": peer_id,
                "public_host": public_host,
                "public_port": public_port,
                "signal": signal_payload or {},
            },
        })
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            if rendezvous_host and rendezvous_port:
                await self.direct_peer.udp_send({"type": "holepunch_ping", "from_id": self.hermes.peer_id, "peer_id": peer_id}, rendezvous_host, rendezvous_port)
            await asyncio.sleep(0.25)
        if self.direct_peer.udp_socket:
            return Connection(peer_id=peer_id, kind="holepunch", udp_socket=self.direct_peer.udp_socket, udp_peer=(public_host, public_port))
        return None


class HermesClient:
    """Relay client for Hermes server."""

    def __init__(self, host: str, port: int, peer_id: str):
        self.host = host
        self.port = port
        self.peer_id = peer_id
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.on_message: Callable[[dict], None] | None = None
        self._backoff = 1.0
        self._max_backoff = 30.0
        self._reconnect_task: asyncio.Task | None = None
        self._closing = False

    async def connect(self):
        if self._closing:
            return self
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            await write_message(self.writer, {"type": "register", "peer_id": self.peer_id, "channels": []})
            self._backoff = 1.0
            asyncio.create_task(self._receive_loop())
            return self
        except OSError as e:
            print(f"Failed to connect to Hermes: {e}")
            self._schedule_reconnect()
            return self

    async def _receive_loop(self):
        try:
            while self.reader and self.writer and not self._closing:
                msg = await read_message(self.reader)
                if msg is None:
                    break
                if self.on_message:
                    self.on_message(msg)
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"Hermes receive error: {e}")
        finally:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._closing:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_later())

    async def _reconnect_later(self):
        if self._closing:
            return
        try:
            await asyncio.sleep(self._backoff)
        except asyncio.CancelledError:
            return
        if self._closing:
            return
        self._backoff = min(self._backoff * 2, self._max_backoff)
        await self.connect()

    async def close(self) -> None:
        self._closing = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reconnect_task
        self._reconnect_task = None
        if self.writer:
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()
        self.reader = None
        self.writer = None

    async def send(self, msg: dict[str, Any]):
        if self.writer:
            ok = await write_message(self.writer, {"type": "relay", "to": msg.get("to"), "body": msg})
            if not ok:
                self._schedule_reconnect()


class TransportManager:
    """Coordinates all transport strategies."""

    def __init__(self, config: Any, identity: Any):
        self.config = config
        self.identity = identity
        self.direct_peer = DirectPeer()
        self.hermes = HermesClient(config.hermes_host, config.hermes_port, identity.peer_id)
        self.cloud = FirebaseCloudQueueClient(config, identity)
        self.holepuncher = HolePuncher(self.direct_peer, self.hermes, config.stun_host, config.stun_port, config.holepunch_timeout_s)
        self.connections: dict[str, Connection] = {}
        self.status: dict[str, Any] = {
            "direct_port": None,
            "udp_port": None,
            "hermes_connected": False,
            "last_transport": None,
            "last_error": None,
        }

    def set_on_message(self, callback: Callable[[dict], None]) -> None:
        self.direct_peer.on_message = callback
        self.hermes.on_message = callback

    async def initialize(self):
        """Start listening and connect to Hermes."""
        port = await self.direct_peer.listen(port=0)
        self.status["direct_port"] = port
        self.status["udp_port"] = await self.direct_peer.listen_udp(0)
        print(f"Listening on local port {port}")
        await self.hermes.connect()
        self.status["hermes_connected"] = self.hermes.writer is not None

    async def connect(self, peer_id: str, hint_host: str | None = None, hint_port: int | None = None) -> Connection:
        mode = self.config.transport_mode
        self.status["last_error"] = None

        if mode == "all_relay":
            conn = Connection(peer_id=peer_id, kind="relay")
            self.connections[peer_id] = conn
            self.status["last_transport"] = "relay"
            return conn

        if mode in ("direct_only", "fallback", "all_p2p") and hint_host and hint_port:
            conn = await self.direct_peer.connect(hint_host, hint_port, self.config.direct_timeout_s)
            if conn:
                conn.peer_id = peer_id
                self.connections[peer_id] = conn
                self.status["last_transport"] = "direct"
                return conn

        if mode in ("fallback", "all_p2p"):
            conn = await self.holepuncher.punch(peer_id, hint_host, hint_port)
            if conn:
                self.connections[peer_id] = conn
                self.status["last_transport"] = "holepunch"
                return conn

        if mode == "fallback":
            conn = Connection(peer_id=peer_id, kind="relay")
            self.connections[peer_id] = conn
            self.status["last_transport"] = "relay"
            return conn

        raise RuntimeError(f"Cannot connect to {peer_id} with mode {mode}")

    async def send(self, connection: Connection | None, msg: dict):
        """Send a message via the connection."""
        if connection and connection.writer:
            ok = await write_message(connection.writer, msg)
            if not ok:
                self.status["last_error"] = "direct send failed"
                await self.hermes.send(msg)
                self.status["last_transport"] = "relay"
        elif connection and connection.kind == "holepunch" and connection.udp_socket and connection.udp_peer:
            host, port = connection.udp_peer
            ok = await self.direct_peer.udp_send(msg, host, port)
            if not ok:
                self.status["last_error"] = "udp send failed"
                if self.config.cloud.enabled:
                    ok = await self.cloud.enqueue(msg)
                    if not ok:
                        await self.hermes.send(msg)
                else:
                    await self.hermes.send(msg)
                self.status["last_transport"] = "relay"
            else:
                self.status["last_transport"] = "holepunch"
        else:
            if self.config.cloud.enabled:
                ok = await self.cloud.enqueue(msg)
                if not ok:
                    await self.hermes.send(msg)
                    self.status["last_transport"] = "relay"
                else:
                    self.status["last_transport"] = "cloud"
            else:
                await self.hermes.send(msg)
                self.status["last_transport"] = "relay"

    async def broadcast(self, msg: dict):
        """Broadcast via Hermes or cloud queue when enabled."""
        if self.config.cloud.enabled:
            ok = await self.cloud.enqueue(msg)
            if not ok:
                await self.hermes.send(msg)
                self.status["last_transport"] = "relay"
            else:
                self.status["last_transport"] = "cloud"
        else:
            await self.hermes.send(msg)
            self.status["last_transport"] = "relay"

    async def join_channel(self, name: str):
        """Join a channel on Hermes."""
        if self.hermes.writer:
            await write_message(self.hermes.writer, {"type": "join", "channel": name})

    async def leave_channel(self, name: str):
        """Leave a channel on Hermes."""
        if self.hermes.writer:
            await write_message(self.hermes.writer, {"type": "leave", "channel": name})

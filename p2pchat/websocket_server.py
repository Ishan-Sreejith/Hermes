from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger("websocket")

try:
    import websockets

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False


class WebSocketClient:
    def __init__(self, websocket, client_id: str, user_id: str, username: str):
        self.websocket = websocket
        self.client_id = client_id
        self.user_id = user_id
        self.username = username
        self.channels: set[str] = set()
        self.peer_id: str | None = None
        self.last_ping: float = time.time()
        self.metadata: dict = {}

    async def send_json(self, data: dict):
        try:
            await self.websocket.send(json.dumps(data))
        except Exception as e:
            logger.debug(f"Failed to send to {self.client_id}: {e}")

    async def send_raw(self, data: str):
        try:
            await self.websocket.send(data)
        except Exception as e:
            logger.debug(f"Failed to send raw to {self.client_id}: {e}")

    async def ping(self):
        self.last_ping = time.time()
        await self.send_json({"type": "ping", "ts": self.last_ping})


class WebSocketServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8081):
        self.host = host
        self.port = port
        self._clients: dict[str, WebSocketClient] = {}
        self._channels: dict[str, set[str]] = {}
        self._user_sockets: dict[str, str] = {}
        self._handlers: dict[str, Callable] = {}
        self._running = False
        self._server = None
        self._ping_interval = 30

    def register_handler(self, event_type: str, handler: Callable):
        self._handlers[event_type] = handler

    def _get_client(self, client_id: str) -> WebSocketClient | None:
        return self._clients.get(client_id)

    def _get_client_by_user(self, user_id: str) -> WebSocketClient | None:
        client_id = self._user_sockets.get(user_id)
        if client_id:
            return self._clients.get(client_id)
        return None

    async def _handle_message(self, client: WebSocketClient, data: dict):
        msg_type = data.get("type", "unknown")

        if msg_type in self._handlers:
            handler = self._handlers[msg_type]
            try:
                await handler(client, data)
            except Exception as e:
                logger.error(f"Handler error for {msg_type}: {e}")
                await client.send_json(
                    {"type": "error", "message": f"Handler error: {str(e)}"}
                )
            return

        if msg_type == "auth":
            await self._handle_auth(client, data)
        elif msg_type == "join":
            await self._handle_join(client, data)
        elif msg_type == "leave":
            await self._handle_leave(client, data)
        elif msg_type == "message":
            await self._handle_message_send(client, data)
        elif msg_type == "typing":
            await self._handle_typing(client, data)
        elif msg_type == "reaction":
            await self._handle_reaction(client, data)
        elif msg_type == "read":
            await self._handle_read(client, data)
        elif msg_type == "ping":
            await client.send_json({"type": "pong", "ts": time.time()})
        elif msg_type == "subscribe":
            await self._handle_subscribe(client, data)
        elif msg_type == "unsubscribe":
            await self._handle_unsubscribe(client, data)

    async def _handle_auth(self, client: WebSocketClient, data: dict):
        user_id = data.get("user_id")
        username = data.get("username")
        peer_id = data.get("peer_id")

        if not user_id or not username:
            await client.send_json(
                {"type": "auth_error", "message": "user_id and username required"}
            )
            return

        old_client_id = self._user_sockets.get(user_id)
        if old_client_id and old_client_id in self._clients:
            old_client = self._clients[old_client_id]
            old_client.user_id = None
            old_client.peer_id = None

        client.user_id = user_id
        client.username = username
        client.peer_id = peer_id or user_id
        self._user_sockets[user_id] = client.client_id

        await client.send_json(
            {"type": "auth_ok", "client_id": client.client_id, "user_id": user_id}
        )
        logger.info(f"Client authenticated: {username} ({user_id})")

    async def _handle_join(self, client: WebSocketClient, data: dict):
        channel = data.get("channel")
        if not channel:
            await client.send_json({"type": "error", "message": "channel required"})
            return

        if channel not in self._channels:
            self._channels[channel] = set()

        self._channels[channel].add(client.client_id)
        client.channels.add(channel)

        await client.send_json({"type": "joined", "channel": channel})

        await self._broadcast_to_channel(
            channel,
            {
                "type": "member_joined",
                "channel": channel,
                "user_id": client.user_id,
                "username": client.username,
            },
            exclude=client.client_id,
        )

    async def _handle_leave(self, client: WebSocketClient, data: dict):
        channel = data.get("channel")
        if not channel:
            return

        if channel in self._channels:
            self._channels[channel].discard(client.client_id)
        client.channels.discard(channel)

        await self._broadcast_to_channel(
            channel,
            {
                "type": "member_left",
                "channel": channel,
                "user_id": client.user_id,
                "username": client.username,
            },
        )

    async def _handle_message_send(self, client: WebSocketClient, data: dict):
        channel = data.get("channel")
        body = data.get("body")

        if not channel or not body:
            await client.send_json(
                {"type": "error", "message": "channel and body required"}
            )
            return

        msg = {
            "type": "message",
            "id": str(uuid4()),
            "from_id": client.user_id,
            "from_name": client.username,
            "body": body,
            "channel": channel,
            "ts": time.time(),
            "source": "websocket",
        }

        await self._broadcast_to_channel(channel, msg)

        await client.send_json({"type": "message_sent", "id": msg["id"]})

    async def _handle_typing(self, client: WebSocketClient, data: dict):
        channel = data.get("channel")
        if not channel:
            return

        await self._broadcast_to_channel(
            channel,
            {
                "type": "typing",
                "user_id": client.user_id,
                "username": client.username,
                "channel": channel,
                "ts": time.time(),
            },
            exclude=client.client_id,
        )

    async def _handle_reaction(self, client: WebSocketClient, data: dict):
        msg_id = data.get("message_id")
        emoji = data.get("emoji")
        action = data.get("action", "add")

        if not msg_id or not emoji:
            return

        response = {
            "type": "reaction",
            "message_id": msg_id,
            "user_id": client.user_id,
            "username": client.username,
            "emoji": emoji,
            "action": action,
        }

        if data.get("channel"):
            await self._broadcast_to_channel(data["channel"], response)
        else:
            await client.send_json(response)

    async def _handle_read(self, client: WebSocketClient, data: dict):
        msg_id = data.get("message_id")
        channel = data.get("channel")

        if not msg_id or not channel:
            return

        await self._broadcast_to_channel(
            channel,
            {
                "type": "read_receipt",
                "message_id": msg_id,
                "user_id": client.user_id,
                "username": client.username,
                "channel": channel,
            },
            exclude=client.client_id,
        )

    async def _handle_subscribe(self, client: WebSocketClient, data: dict):
        channel = data.get("channel")
        if channel:
            if channel not in self._channels:
                self._channels[channel] = set()
            self._channels[channel].add(client.client_id)
            client.channels.add(channel)

    async def _handle_unsubscribe(self, client: WebSocketClient, data: dict):
        channel = data.get("channel")
        if channel:
            if channel in self._channels:
                self._channels[channel].discard(client.client_id)
            client.channels.discard(channel)

    async def _broadcast_to_channel(
        self, channel: str, message: dict, exclude: str | None = None
    ):
        if channel not in self._channels:
            return

        for client_id in list(self._channels[channel]):
            if exclude and client_id == exclude:
                continue
            client = self._clients.get(client_id)
            if client:
                await client.send_json(message)

    async def _broadcast_all(self, message: dict):
        for client in list(self._clients.values()):
            await client.send_json(message)

    async def _ping_loop(self):
        while self._running:
            await asyncio.sleep(self._ping_interval)
            now = time.time()
            for client_id, client in list(self._clients.items()):
                if now - client.last_ping > self._ping_interval * 2:
                    logger.info(f"Client {client_id} ping timeout")
                    try:
                        del self._clients[client_id]
                        if client.user_id:
                            del self._user_sockets[client.user_id]
                    except KeyError:
                        pass

    async def _client_handler(self, websocket, path: str):
        client_id = str(uuid4())
        client = WebSocketClient(websocket, client_id, "anonymous", "Anonymous")
        self._clients[client_id] = client

        logger.info(f"New WebSocket connection: {client_id}")

        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                    await self._handle_message(client, data)
                except json.JSONDecodeError:
                    logger.debug(f"Invalid JSON from {client_id}")
                    await client.send_json({"type": "error", "message": "Invalid JSON"})
        except Exception as e:
            logger.debug(f"Client {client_id} disconnected: {e}")
        finally:
            for channel in list(client.channels):
                self._channels[channel].discard(client_id)
            if client.user_id:
                self._user_sockets.pop(client.user_id, None)
            self._clients.pop(client_id, None)
            logger.info(f"Client disconnected: {client_id}")

    async def start(self):
        if not WEBSOCKETS_AVAILABLE:
            logger.warning("websockets library not available")
            return False

        self._running = True
        async with websockets.serve(self._client_handler, self.host, self.port):
            logger.info(f"WebSocket server started on {self.host}:{self.port}")
            asyncio.create_task(self._ping_loop())
            await asyncio.Future()

    def stop(self):
        self._running = False


class WebSocketClientWrapper:
    def __init__(self, uri: str):
        self.uri = uri
        self._socket = None
        self._handlers: dict[str, list[Callable]] = {
            "message": [],
            "typing": [],
            "reaction": [],
            "read_receipt": [],
            "member_joined": [],
            "member_left": [],
            "error": [],
        }

    def on(self, event: str, handler: Callable):
        if event in self._handlers:
            self._handlers[event].append(handler)

    def off(self, event: str, handler: Callable):
        if event in self._handlers:
            self._handlers[event].remove(handler)

    async def connect(self):
        if not WEBSOCKETS_AVAILABLE:
            raise RuntimeError("websockets library not available")
        self._socket = await websockets.connect(self.uri)
        asyncio.create_task(self._receive_loop())

    async def _receive_loop(self):
        async for raw in self._socket:
            try:
                data = json.loads(raw)
                msg_type = data.get("type", "unknown")
                if msg_type in self._handlers:
                    for handler in self._handlers[msg_type]:
                        try:
                            await handler(data)
                        except Exception:
                            pass
            except json.JSONDecodeError:
                pass

    async def send(self, data: dict):
        if self._socket:
            await self._socket.send(json.dumps(data))

    async def send_message(self, channel: str, body: str):
        await self.send({"type": "message", "channel": channel, "body": body})

    async def send_typing(self, channel: str):
        await self.send({"type": "typing", "channel": channel})

    async def send_reaction(
        self, message_id: str, emoji: str, action: str = "add", channel: str = None
    ):
        await self.send(
            {
                "type": "reaction",
                "message_id": message_id,
                "emoji": emoji,
                "action": action,
                "channel": channel,
            }
        )

    async def send_read(self, message_id: str, channel: str):
        await self.send({"type": "read", "message_id": message_id, "channel": channel})

    async def join_channel(self, channel: str):
        await self.send({"type": "join", "channel": channel})

    async def leave_channel(self, channel: str):
        await self.send({"type": "leave", "channel": channel})

    async def close(self):
        if self._socket:
            await self._socket.close()

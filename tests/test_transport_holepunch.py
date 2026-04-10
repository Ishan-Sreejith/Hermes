from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest

from p2pchat.transport import DirectPeer, HolePuncher, HermesClient, TransportManager


class DummyHermes(HermesClient):
    def __init__(self):
        super().__init__("127.0.0.1", 7777, "alice")
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)


class DummyIdentity:
    peer_id = "alice"
    username = "alice"


@pytest.mark.asyncio
async def test_direct_peer_udp_send_roundtrip():
    peer = DirectPeer()
    port = await peer.listen_udp(0)
    received = []
    peer.on_message = lambda msg: received.append(msg)

    other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    other.settimeout(1.0)
    try:
        other.sendto(b'{"type":"ping","hello":"world"}', ("127.0.0.1", port))
        for _ in range(10):
            if received:
                break
            await asyncio.sleep(0.05)
    finally:
        other.close()
        await peer.close_udp()

    assert received
    assert received[0]["type"] == "ping"


@pytest.mark.asyncio
async def test_holepuncher_returns_none_when_stun_unreachable(monkeypatch):
    peer = DirectPeer()
    hermes = DummyHermes()
    puncher = HolePuncher(peer, hermes, "127.0.0.1", 1, timeout_s=0)

    async def fake_discover(port):
        return None

    monkeypatch.setattr(puncher, "_stun_discover", fake_discover)
    result = await puncher.punch("bob", "127.0.0.1", 7777)
    assert result is None


@pytest.mark.asyncio
async def test_transport_manager_holepunch_readiness_smoke(monkeypatch):
    cfg = SimpleNamespace(
        transport_mode="fallback",
        direct_timeout_s=1,
        holepunch_timeout_s=1,
        hermes_host="127.0.0.1",
        hermes_port=7777,
        stun_host="127.0.0.1",
        stun_port=1,
        cloud=SimpleNamespace(enabled=False),
    )
    manager = TransportManager(cfg, DummyIdentity())
    manager.hermes = DummyHermes()

    async def fake_connect(*args, **kwargs):
        return None

    async def fake_punch(*args, **kwargs):
        return None

    monkeypatch.setattr(manager.direct_peer, "connect", fake_connect)
    monkeypatch.setattr(manager.holepuncher, "punch", fake_punch)
    await manager.initialize()

    assert manager.status["direct_port"] is not None
    assert manager.status["udp_port"] is not None
    assert manager.status["hermes_connected"] is True

    conn = await manager.connect("bob")
    assert conn.kind == "relay"
    assert manager.status["last_transport"] == "relay"

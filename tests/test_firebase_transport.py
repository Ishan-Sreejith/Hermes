from __future__ import annotations

import pytest

from p2pchat.config import Config, CloudConfig
from p2pchat.transport import FirebaseCloudQueueClient


class DummyIdentity:
    peer_id = "alice"
    username = "alice"


@pytest.mark.asyncio
async def test_firebase_queue_client_disabled_returns_false(tmp_path):
    cfg = Config(cloud=CloudConfig(enabled=False, backend="firebase"))
    client = FirebaseCloudQueueClient(cfg, DummyIdentity())
    assert await client.enqueue({"to": "bob", "body": "hi"}) is False
    assert await client.poll(lambda msg: None) == 0
    assert await client.delete("msg-1") is False


@pytest.mark.asyncio
async def test_firebase_queue_payload_shape(tmp_path):
    cfg = Config(cloud=CloudConfig(enabled=True, backend="firebase", queue_path="queue", delivery_ttl_s=10))
    client = FirebaseCloudQueueClient(cfg, DummyIdentity())
    payload = client._message_payload({"to": "bob", "body": "hi", "type": "msg", "enc": "none"})
    assert payload["to"] == "bob"
    assert payload["body"] == "hi"
    assert payload["expires_at"] > payload["ts"]
    assert client._recipient_path("bob") == "queue/bob"

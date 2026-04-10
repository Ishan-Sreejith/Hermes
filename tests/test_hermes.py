import asyncio

import pytest

from p2pchat.hermes_server import handle_client, reset_state
from p2pchat.protocol import read_message, write_message


@pytest.fixture
async def hermes_server_instance():
    reset_state()
    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield ("127.0.0.1", port)
    finally:
        server.close()
        await server.wait_closed()
        reset_state()


@pytest.mark.asyncio
async def test_hermes_broadcast(hermes_server_instance):
    host, port = hermes_server_instance

    reader_a, writer_a = await asyncio.open_connection(host, port)
    reader_b, writer_b = await asyncio.open_connection(host, port)

    try:
        await write_message(writer_a, {"type": "register", "peer_id": "alice", "channels": ["#test"]})
        await write_message(writer_b, {"type": "register", "peer_id": "bob", "channels": ["#test"]})
        await asyncio.sleep(0.05)

        payload = {
            "type": "msg",
            "from_id": "alice",
            "from_name": "alice",
            "to": "*",
            "channel": "#test",
            "body": "hello relay",
            "enc": "none",
            "ts": 1,
        }
        await write_message(writer_a, {"type": "relay", "to": "*", "body": payload})

        msg = await asyncio.wait_for(read_message(reader_b), timeout=2)
        assert msg is not None
        assert msg["body"] == "hello relay"
        assert msg["from_name"] == "alice"
    finally:
        writer_a.close()
        writer_b.close()
        await writer_a.wait_closed()
        await writer_b.wait_closed()

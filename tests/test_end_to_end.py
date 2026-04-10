import asyncio
import threading
import time

from p2pchat.hermes_server import handle_client, reset_state
from p2pchat.protocol import read_message, write_message
from p2pchat.web_ui import app as web_app


def _start_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


async def test_web_ui_to_relay_end_to_end():
    reset_state()
    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]

    thread_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=_start_loop, args=(thread_loop,), daemon=True)
    thread.start()

    reader_peer = writer_peer = None
    try:
        web_app.message_queue.clear()
        web_app.known_peers.clear()
        web_app.hermes_host = host
        web_app.hermes_port = int(port)
        web_app.loop = thread_loop
        web_app.hermes_reader = None
        web_app.hermes_writer = None

        fut = asyncio.run_coroutine_threadsafe(web_app.connect_hermes(), thread_loop)
        fut.result(timeout=3)

        reader_peer, writer_peer = await asyncio.open_connection(host, port)
        await write_message(writer_peer, {"type": "register", "peer_id": "bob", "channels": ["#test"]})
        await asyncio.sleep(0.05)

        client = web_app.app.test_client()
        send_resp = client.post(
            "/send",
            json={"body": "hello from web", "to": "*", "channel": "#test", "enc": "none", "ts": time.time()},
        )
        assert send_resp.status_code == 200
        assert send_resp.get_json()["ok"] is True

        relayed = await asyncio.wait_for(read_message(reader_peer), timeout=2)
        assert relayed is not None
        assert relayed["body"] == "hello from web"
        assert relayed["from_name"] == "web-ui"

        messages_resp = client.get("/messages?since=0")
        assert messages_resp.status_code == 200
        all_messages = messages_resp.get_json()
        assert any(m.get("body") == "hello from web" for m in all_messages)
    finally:
        if writer_peer is not None:
            writer_peer.close()
            await writer_peer.wait_closed()

        if web_app.hermes_writer is not None:
            close_future = asyncio.run_coroutine_threadsafe(_close_writer(web_app.hermes_writer), thread_loop)
            close_future.result(timeout=2)

        thread_loop.call_soon_threadsafe(thread_loop.stop)
        thread.join(timeout=2)

        server.close()
        await server.wait_closed()
        reset_state()


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    await writer.wait_closed()

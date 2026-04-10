from p2pchat.protocol import build, decode_frame, encode


def test_protocol_roundtrip():
    msg = build("msg", "hello", "id1", "alice", "#test", None, "none")
    encoded = encode(msg)
    decoded = decode_frame(encoded)
    assert decoded["body"] == "hello"
    assert decoded["from_name"] == "alice"

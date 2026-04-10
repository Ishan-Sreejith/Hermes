from p2pchat.identity import load_or_create


def test_identity_create_and_reload(tmp_path):
    identity = load_or_create(tmp_path, "test_user")
    assert identity.peer_id
    assert identity.username == "test_user"

    reloaded = load_or_create(tmp_path, "other_name")
    assert reloaded.peer_id == identity.peer_id
    assert reloaded.username == "test_user"

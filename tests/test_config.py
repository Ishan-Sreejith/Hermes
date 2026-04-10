from p2pchat.config import ConfigManager


def test_config_roundtrip(tmp_path):
    cm = ConfigManager(tmp_path)
    config = cm.load()
    config.transport_mode = "all_relay"
    config.crypto.default_mode = "fernet"
    cm.save(config)

    loaded = cm.load()
    assert loaded.transport_mode == "all_relay"
    assert loaded.crypto.default_mode == "fernet"

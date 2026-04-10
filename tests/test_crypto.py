from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization

from p2pchat.crypto import CryptoManager
from p2pchat.identity import load_or_create


def test_fernet_roundtrip(tmp_path):
    identity = load_or_create(tmp_path / "alice", "alice")
    crypto = CryptoManager(identity, known_peers_path=tmp_path / "known.json")

    key = Fernet.generate_key().decode("ascii")
    crypto.set_peer_key("bob", key)

    plaintext = "hello bob"
    ciphertext, enc = crypto.encrypt(plaintext, "bob", "fernet")
    decrypted = crypto.decrypt(ciphertext, enc, "bob")
    assert decrypted == plaintext


def test_rsa_roundtrip(tmp_path):
    alice = load_or_create(tmp_path / "alice", "alice")
    bob = load_or_create(tmp_path / "bob", "bob")
    crypto = CryptoManager(alice, known_peers_path=tmp_path / "known.json")

    pem = bob.public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    crypto.set_peer_key("bob", pem)

    plaintext = "hello rsa"
    ciphertext, enc = crypto.encrypt(plaintext, "bob", "rsa")

    bob_crypto = CryptoManager(bob, known_peers_path=tmp_path / "known-bob.json")
    decrypted = bob_crypto.decrypt(ciphertext, enc, "alice")
    assert decrypted == plaintext


def test_plugin_roundtrip(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "reverse_cipher.py").write_text(
        """
class Plugin:
    name = \"reverse\"

    def encrypt(self, plaintext: bytes, key: bytes) -> bytes:
        return plaintext[::-1]

    def decrypt(self, ciphertext: bytes, key: bytes) -> bytes:
        return ciphertext[::-1]
""".strip()
    )

    identity = load_or_create(tmp_path / "alice", "alice")
    crypto = CryptoManager(
        identity,
        known_peers_path=tmp_path / "known.json",
        plugin_dir=plugin_dir,
    )

    plaintext = "hello plugin"
    ciphertext, enc = crypto.encrypt(plaintext, "peer-x", "plugin:reverse")
    decrypted = crypto.decrypt(ciphertext, enc, "peer-x")
    assert decrypted == plaintext

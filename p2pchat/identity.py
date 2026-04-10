from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ModuleNotFoundError:  # pragma: no cover
    hashes = serialization = padding = rsa = None


DEFAULT_USERNAME = "guest"


@dataclass
class Identity:
    peer_id: str
    username: str
    public_key: object
    private_key: object

    @property
    def public_key_bytes(self) -> bytes:
        if serialization is None:
            raise RuntimeError("cryptography is required for key serialization")
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def sign(self, data: bytes) -> bytes:
        if padding is None or hashes is None:
            raise RuntimeError("cryptography is required for signing")
        return self.private_key.sign(
            data,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )


def _identity_path(config_dir: Path) -> Path:
    return config_dir / "identity.json"


def load_or_create(config_dir: Path, username: str | None = None) -> Identity:
    if serialization is None or rsa is None:
        raise RuntimeError("Please install cryptography to use P2PChat")
    config_dir.mkdir(parents=True, exist_ok=True)
    path = _identity_path(config_dir)
    if path.exists():
        raw = path.read_text()
        import json

        data = json.loads(raw)
        private_key = serialization.load_pem_private_key(data["private_key"].encode("utf-8"), password=None)
        public_key = serialization.load_pem_public_key(data["public_key"].encode("utf-8"))
        return Identity(data["peer_id"], data.get("username", DEFAULT_USERNAME), public_key, private_key)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    identity = Identity(peer_id=str(uuid.uuid4()), username=username or os.environ.get("P2PCHAT_USERNAME", DEFAULT_USERNAME), public_key=public_key, private_key=private_key)
    import json

    path.write_text(
        json.dumps(
            {
                "peer_id": identity.peer_id,
                "username": identity.username,
                "public_key": identity.public_key_bytes.decode("utf-8"),
                "private_key": identity.private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                ).decode("utf-8"),
            },
            indent=2,
        )
    )
    return identity

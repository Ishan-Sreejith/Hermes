from __future__ import annotations

import base64
import importlib.util
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .identity import Identity

logger = logging.getLogger("crypto")

@dataclass
class PluginWrapper:
    name: str
    instance: Any


class CryptoManager:
    def __init__(self, identity: Identity, known_peers_path: Path | None = None, plugin_dir: Path | None = None):
        self.identity = identity
        self.known_peers_path = known_peers_path or Path.home() / ".p2pchat" / "known_peers.json"
        self.known_peers_path.parent.mkdir(parents=True, exist_ok=True)
        self.known_peers = self._load_known_peers()
        self.plugins = self.load_plugins(plugin_dir or (Path.home() / ".p2pchat" / "plugins"))

    def _load_known_peers(self) -> dict[str, dict[str, str]]:
        if self.known_peers_path.exists():
            try:
                return json.loads(self.known_peers_path.read_text())
            except Exception:
                return {}
        return {}

    def _save_known_peers(self) -> None:
        self.known_peers_path.write_text(json.dumps(self.known_peers, indent=2))

    @staticmethod
    def _looks_like_pem(value: str) -> bool:
        return "BEGIN PUBLIC KEY" in value or "BEGIN RSA PUBLIC KEY" in value

    @staticmethod
    def _looks_like_fernet_key(value: str) -> bool:
        try:
            raw = base64.urlsafe_b64decode(value.encode("ascii"))
            return len(raw) == 32
        except Exception:
            return False

    def set_peer_key(self, peer_id: str, key_material: str | Any) -> str:
        if hasattr(key_material, "public_bytes"):
            key_material = key_material.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")

        key_str = str(key_material).strip()
        entry = self.known_peers.setdefault(peer_id, {})
        if self._looks_like_pem(key_str):
            entry["public_key"] = key_str
            key_kind = "rsa"
        elif self._looks_like_fernet_key(key_str):
            entry["fernet_key"] = key_str
            key_kind = "fernet"
        else:
            raise ValueError("key must be a PEM RSA public key or a URL-safe base64 Fernet key")

        self._save_known_peers()
        return key_kind

    def _fallback_fernet_key(self) -> bytes:
        # Stable fallback key derived from identity to allow some level of "default" encryption
        return base64.urlsafe_b64encode(self.identity.peer_id.encode("utf-8").ljust(32, b"0")[:32])

    def _fernet_key_for_peer(self, peer_id: str) -> bytes:
        peer = self.known_peers.get(peer_id, {})
        key = peer.get("fernet_key")
        if key and self._looks_like_fernet_key(key):
            return key.encode("ascii")
        return self._fallback_fernet_key()

    @staticmethod
    def normalize_mode(mode: str) -> str:
        aliases = {
            "symmetric": "fernet",
            "asymmetric": "rsa",
        }
        return aliases.get(mode, mode)

    def encrypt(self, body: str, peer_id: str, mode: str) -> tuple[str, str]:
        mode = self.normalize_mode(mode)

        if mode == "none":
            return body, "none"

        # If we are broadcasting or sending to a channel, and RSA is selected,
        # we must fallback to something broadcast-compatible (plain or symmetric)
        # because RSA is point-to-point.
        if (peer_id == "*" or peer_id.startswith("@")) and mode == "rsa":
            logger.warning(f"RSA encryption requested for {peer_id}, falling back to plain.")
            return body, "none"

        if mode == "fernet":
            token = Fernet(self._fernet_key_for_peer(peer_id)).encrypt(body.encode("utf-8"))
            return base64.b64encode(token).decode("ascii"), "fernet"

        if mode == "rsa":
            peer = self.known_peers.get(peer_id)
            if not peer or "public_key" not in peer:
                # Instead of crashing, let's inform the user and suggest a fix
                raise ValueError(f"Unknown RSA public key for {peer_id}. Use '/key {peer_id} <PEM>' to set it, or switch to '/crypto fernet' or '/crypto none'.")
            
            public_key = serialization.load_pem_public_key(peer["public_key"].encode("utf-8"))
            ciphertext = public_key.encrypt(
                body.encode("utf-8"),
                padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
            )
            return base64.b64encode(ciphertext).decode("ascii"), "rsa"

        if mode.startswith("custom:") or mode.startswith("plugin:"):
            plugin_name = mode.split(":", 1)[1]
            plugin = next((p for p in self.plugins if p.name == plugin_name), None)
            if not plugin:
                raise ValueError(f"unknown plugin {plugin_name}")
            ciphertext = plugin.instance.encrypt(body.encode("utf-8"), b"")
            return base64.b64encode(ciphertext).decode("ascii"), f"custom:{plugin_name}"

        raise ValueError(f"unsupported mode {mode}")

    def decrypt(self, body: str, enc: str, peer_id: str) -> str:
        enc = self.normalize_mode(enc)

        if enc == "none":
            return body
        if enc == "fernet":
            try:
                plaintext = Fernet(self._fernet_key_for_peer(peer_id)).decrypt(base64.b64decode(body.encode("ascii")))
                return plaintext.decode("utf-8")
            except Exception:
                return f"[Decryption Error: Invalid Fernet key for {peer_id}]"
        if enc == "rsa":
            try:
                plaintext = self.identity.private_key.decrypt(
                    base64.b64decode(body.encode("ascii")),
                    padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
                )
                return plaintext.decode("utf-8")
            except Exception:
                return "[Decryption Error: RSA decryption failed]"
        if enc.startswith("custom:"):
            plugin_name = enc.split(":", 1)[1]
            plugin = next((p for p in self.plugins if p.name == plugin_name), None)
            if not plugin:
                return f"[Decryption Error: Plugin {plugin_name} missing]"
            plaintext = plugin.instance.decrypt(base64.b64decode(body.encode("ascii")), b"")
            return plaintext.decode("utf-8")
        return body

    def load_plugins(self, plugin_dir: Path) -> list[PluginWrapper]:
        loaded: list[PluginWrapper] = []
        if not plugin_dir.exists():
            return loaded
        for path in plugin_dir.glob("*.py"):
            try:
                spec = importlib.util.spec_from_file_location(path.stem, path)
                if not spec or not spec.loader:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and hasattr(attr, "name"):
                        inst = attr()
                        loaded.append(PluginWrapper(name=inst.name, instance=inst))
                        break
            except Exception:
                continue
        return loaded

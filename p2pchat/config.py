from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class CryptoConfig:
    default_mode: str = "none"
    allow_plaintext: bool = True
    active_plugin: str | None = None


@dataclass
class UIConfig:
    timestamp: bool = True
    show_peer_id: bool = False
    compact_tui: bool = True
    hosted_web_ui_url: str | None = None


@dataclass
class CloudConfig:
    backend: str = "firebase"
    enabled: bool = False
    project_id: str | None = None
    database_url: str | None = None
    queue_path: str = "messages"
    delivery_ttl_s: int = 300
    credentials_path: str | None = None
    service_account_json: str | None = None
    hosting_enabled: bool = False
    hosting_site: str | None = None


@dataclass
class Config:
    transport_mode: str = "fallback"
    fallback_order: list[str] = field(default_factory=lambda: ["direct", "holepunch", "hermes"])
    direct_timeout_s: int = 3
    holepunch_timeout_s: int = 5
    hermes_host: str = "127.0.0.1"
    hermes_port: int = 7777
    stun_host: str = "stun.l.google.com"
    stun_port: int = 19302
    crypto: CryptoConfig = field(default_factory=CryptoConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)


class ConfigManager:
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.path = config_dir / "config.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> Config:
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            return Config(
                transport_mode=raw.get("transport_mode", "fallback"),
                fallback_order=raw.get("fallback_order", ["direct", "holepunch", "hermes"]),
                direct_timeout_s=raw.get("direct_timeout_s", 3),
                holepunch_timeout_s=raw.get("holepunch_timeout_s", 5),
                hermes_host=raw.get("hermes_host", "127.0.0.1"),
                hermes_port=raw.get("hermes_port", 7777),
                stun_host=raw.get("stun_host", "stun.l.google.com"),
                stun_port=raw.get("stun_port", 19302),
                crypto=CryptoConfig(**raw.get("crypto", {})),
                ui=UIConfig(**raw.get("ui", {})),
                cloud=CloudConfig(**raw.get("cloud", {})),
            )
        config = Config()
        self.save(config)
        return config

    def save(self, config: Config) -> None:
        self.path.write_text(json.dumps(asdict(config), indent=2))

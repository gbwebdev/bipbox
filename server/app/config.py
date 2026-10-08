"""Server settings, from the environment.

Everything is overridable by an environment variable so the Docker Compose file
is the single place deployment differs (architecture.md §2).
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BIPBOX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Identity ─────────────────────────────────────────────────────────────
    environment: str = "production"
    protocol_version: int = 1
    """Bumped when the device↔server protocol changes incompatibly.

    A box older than `min_protocol_version` is told to update rather than
    allowed to fail obscurely later (docs/workflow.md, Compatibility).
    """
    min_protocol_version: int = 1

    # ── Storage ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./data/bipbox.db"
    image_dir: str = "./data/images"

    # ── Secrets ──────────────────────────────────────────────────────────────
    session_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    """Signs session cookies. Generated per process if unset, which logs every
    admin out on restart — fine in development, wrong in production."""

    device_secret_pepper: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    """Mixed into device secret HMACs. Changing it invalidates every device."""

    monitor_token: str = ""
    """Read-only token for /health/detail. Empty disables those endpoints
    rather than leaving them open (architecture.md §13)."""

    # ── Media plane ──────────────────────────────────────────────────────────
    janus_url: str = "http://127.0.0.1:8088/janus"
    janus_admin_url: str = "http://127.0.0.1:7088/admin"
    browser_voip_mode: str = "wireguard"
    wg_subnet: str = "100.64.42.0/24"

    @field_validator("browser_voip_mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        allowed = {"off", "wireguard", "plain_rtp"}
        if value not in allowed:
            raise ValueError(f"browser_voip_mode must be one of {sorted(allowed)}")
        return value

    @field_validator("environment")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        allowed = {"development", "production"}
        if value not in allowed:
            raise ValueError(f"environment must be one of {sorted(allowed)}")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def monitoring_enabled(self) -> bool:
        return bool(self.monitor_token)

    def production_warnings(self) -> list[str]:
        """Settings that are safe in development and dangerous in production.

        Returned rather than raised: a server that refuses to boot over a
        missing monitoring token is worse than one that says so loudly.
        """
        if not self.is_production:
            return []
        problems = []
        if "BIPBOX_SESSION_SECRET" not in _env_keys():
            problems.append(
                "session_secret is generated per process — every admin session "
                "is lost on restart. Set BIPBOX_SESSION_SECRET."
            )
        if "BIPBOX_DEVICE_SECRET_PEPPER" not in _env_keys():
            problems.append(
                "device_secret_pepper is generated per process — every device "
                "credential becomes invalid on restart. Set "
                "BIPBOX_DEVICE_SECRET_PEPPER."
            )
        if not self.monitor_token:
            problems.append("monitor_token unset — /health/detail is disabled.")
        return problems


def _env_keys() -> set[str]:
    import os

    return set(os.environ)


@lru_cache
def get_settings() -> Settings:
    return Settings()

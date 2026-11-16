"""Validated environment configuration."""

import os
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class TierLimit:
    capacity: int
    refill_rate: float


@dataclass(frozen=True)
class Settings:
    redis_url: str
    redis_connect_timeout: float
    redis_socket_timeout: float
    redis_failure_mode: str
    api_keys: Dict[str, str]
    tiers: Dict[str, TierLimit]
    cors_origins: tuple

    @classmethod
    def from_env(cls) -> "Settings":
        failure = os.getenv("REDIS_FAILURE_MODE", "closed").lower()
        if failure not in {"open", "closed"}:
            raise ValueError("REDIS_FAILURE_MODE must be open or closed")
        tiers = {
            "free": TierLimit(_positive_int("FREE_CAPACITY", 5), _positive_float("FREE_REFILL_RATE", 1)),
            "premium": TierLimit(_positive_int("PREMIUM_CAPACITY", 50), _positive_float("PREMIUM_REFILL_RATE", 10)),
            "admin": TierLimit(_positive_int("ADMIN_CAPACITY", 100), _positive_float("ADMIN_REFILL_RATE", 25)),
        }
        raw_keys = os.getenv("API_KEYS", "free-key-123:free,premium-key-456:premium")
        keys = {}
        for item in raw_keys.split(","):
            if not item.strip() or ":" not in item:
                raise ValueError("API_KEYS must contain comma-separated key:tier pairs")
            key, tier = item.split(":", 1)
            if tier not in tiers or not key:
                raise ValueError(f"Invalid API key tier: {tier}")
            keys[key] = tier
        origins = tuple(x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",") if x.strip())
        return cls(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                   float(os.getenv("REDIS_CONNECT_TIMEOUT", "1")),
                   float(os.getenv("REDIS_SOCKET_TIMEOUT", "1")), failure,
                   keys, tiers, origins)


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value

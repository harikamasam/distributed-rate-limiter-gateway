import pytest

from app.config import Settings


def test_default_tiers_are_centralized(monkeypatch):
    monkeypatch.delenv("API_KEYS", raising=False)
    settings = Settings.from_env()
    assert settings.tiers["free"].capacity == 5
    assert settings.tiers["premium"].refill_rate == 10


def test_invalid_failure_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("REDIS_FAILURE_MODE", "maybe")
    with pytest.raises(ValueError):
        Settings.from_env()

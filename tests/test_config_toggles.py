"""Tests for central configuration, feature toggles, VIBE configurability, and startup validation."""
import importlib
import pytest
import config


def test_config_defaults_and_branding():
    assert "RISERNOTFOUND" in config.CREDIT
    assert config.REPO_NAME == "ATLAS-NOVA"
    assert config.VIBE_CHANNEL_NAME == "vibe-check"
    assert isinstance(config.FEATURE_VIBE, bool)
    assert isinstance(config.FEATURE_TTS, bool)
    assert isinstance(config.FEATURE_TICKETS, bool)
    assert isinstance(config.FEATURE_AUTOMOD, bool)
    assert isinstance(config.FEATURE_TEMPVOICE, bool)
    assert isinstance(config.FEATURE_ANALYTICS, bool)


def test_feature_toggles_and_vibe_override(monkeypatch):
    monkeypatch.setenv("FEATURE_VIBE", "false")
    monkeypatch.setenv("FEATURE_TTS", "off")
    monkeypatch.setenv("VIBE_CHANNEL_ID", "100000000000000099")
    monkeypatch.setenv("VIBE_CHANNEL_NAME", "custom-vibe")
    importlib.reload(config)
    try:
        assert config.FEATURE_VIBE is False
        assert config.FEATURE_TTS is False
        assert config.VIBE_CHANNEL_ID == 100000000000000099
        assert config.VIBE_CHANNEL_NAME == "custom-vibe"
    finally:
        monkeypatch.delenv("FEATURE_VIBE", raising=False)
        monkeypatch.delenv("FEATURE_TTS", raising=False)
        monkeypatch.delenv("VIBE_CHANNEL_ID", raising=False)
        monkeypatch.delenv("VIBE_CHANNEL_NAME", raising=False)
        importlib.reload(config)


def test_validate_config_missing_token_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setattr(config, "TOKEN_FILE", tmp_path / "missing_token.txt")
    with pytest.raises(config.ConfigError, match="Missing ATLAS bot token"):
        config.validate_config("ATLAS", require_token=True)


def test_validate_config_invalid_tts_rate_raises(monkeypatch):
    monkeypatch.setattr(config, "TTS_RATE", 0)
    with pytest.raises(config.ConfigError, match="positive integers"):
        config.validate_config("ATLAS", require_token=False)

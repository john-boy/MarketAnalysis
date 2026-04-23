"""Tests for the configuration loader."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from market_analysis.services import config as config_mod


@pytest.fixture(autouse=True)
def _clear_cache():
    config_mod.get_settings.cache_clear()
    yield
    config_mod.get_settings.cache_clear()


def _write(p: Path, body: str) -> None:
    p.write_text(dedent(body).lstrip("\n"))


def test_loads_settings_only(tmp_path, monkeypatch):
    settings = tmp_path / "settings.toml"
    _write(
        settings,
        """
        [mongo]
        uri = "mongodb://example:27017"
        database = "Foo"

        [alpha_vantage]
        rate_limit_per_minute = 600
        """,
    )
    monkeypatch.setattr(config_mod, "SETTINGS_PATH", settings)
    monkeypatch.setattr(config_mod, "SECRETS_PATH", tmp_path / "secrets.toml")

    s = config_mod.get_settings()
    assert s.mongo.uri == "mongodb://example:27017"
    assert s.mongo.database == "Foo"
    assert s.alpha_vantage.rate_limit_per_minute == 600
    assert s.alpha_vantage.api_key == ""
    assert not s.has_alpha_vantage_key()


def test_secrets_overlay_and_merge(tmp_path, monkeypatch):
    settings = tmp_path / "settings.toml"
    secrets = tmp_path / "secrets.toml"
    _write(
        settings,
        """
        [alpha_vantage]
        rate_limit_per_minute = 150
        """,
    )
    _write(
        secrets,
        """
        [alpha_vantage]
        api_key = "ABCDEFGHIJKL"

        [schwab]
        client_id = "cid"
        client_secret = "csecret"
        """,
    )
    monkeypatch.setattr(config_mod, "SETTINGS_PATH", settings)
    monkeypatch.setattr(config_mod, "SECRETS_PATH", secrets)

    s = config_mod.get_settings()
    # Merged: non-secret field preserved, secret field overlaid.
    assert s.alpha_vantage.rate_limit_per_minute == 150
    assert s.alpha_vantage.api_key == "ABCDEFGHIJKL"
    assert s.has_alpha_vantage_key()
    assert s.has_schwab_credentials()


def test_redacted_masks_secrets(tmp_path, monkeypatch):
    settings = tmp_path / "settings.toml"
    secrets = tmp_path / "secrets.toml"
    _write(settings, "")
    _write(
        secrets,
        """
        [alpha_vantage]
        api_key = "ABCDEFGHIJKL"

        [schwab]
        client_secret = "longsecretvalue"
        refresh_token = "xy"
        """,
    )
    monkeypatch.setattr(config_mod, "SETTINGS_PATH", settings)
    monkeypatch.setattr(config_mod, "SECRETS_PATH", secrets)

    r = config_mod.get_settings().redacted()
    assert r["alpha_vantage"]["api_key"] == "AB***KL"
    assert r["schwab"]["client_secret"] == "lo***ue"
    # Short secret falls back to fully masked.
    assert r["schwab"]["refresh_token"] == "***"


def test_missing_settings_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "SETTINGS_PATH", tmp_path / "nope.toml")
    monkeypatch.setattr(config_mod, "SECRETS_PATH", tmp_path / "also-nope.toml")
    with pytest.raises(FileNotFoundError):
        config_mod.get_settings()


def test_reload_picks_up_changes(tmp_path, monkeypatch):
    settings = tmp_path / "settings.toml"
    _write(settings, '[mongo]\nuri = "a"\n')
    monkeypatch.setattr(config_mod, "SETTINGS_PATH", settings)
    monkeypatch.setattr(config_mod, "SECRETS_PATH", tmp_path / "secrets.toml")

    assert config_mod.get_settings().mongo.uri == "a"
    _write(settings, '[mongo]\nuri = "b"\n')
    # Cache still holds "a" until reload.
    assert config_mod.get_settings().mongo.uri == "a"
    assert config_mod.reload_settings().mongo.uri == "b"

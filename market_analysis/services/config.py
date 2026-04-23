"""Application configuration loader.

Loads ``config/settings.toml`` (required, non-secret) and overlays
``config/secrets.toml`` (optional, gitignored).  Returns a typed
Pydantic :class:`Settings` object.  The loader is cached; call
:func:`reload_settings` to pick up changes on disk.

Secret fields are masked by :meth:`Settings.redacted` so the settings
object can be safely logged or shown in the Admin tab.
"""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# -- Filesystem layout ----------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
SETTINGS_PATH = CONFIG_DIR / "settings.toml"
SECRETS_PATH = CONFIG_DIR / "secrets.toml"


# -- Typed settings -------------------------------------------------------


class MongoSettings(BaseModel):
    uri: str = "mongodb://localhost:27017"
    database: str = "MarketAnalysis"
    prototype_database: str = "MarketAnalysis_Prototype"


class PollerSettings(BaseModel):
    run_time: str = "18:00"        # HH:MM, market close + 2h
    timezone: str = "America/New_York"
    enabled: bool = True


class IaWriterSettings(BaseModel):
    folder: str = ""  # iCloud path to the iA Writer folder


class AlphaVantageSettings(BaseModel):
    base_url: str = "https://www.alphavantage.co/query"
    rate_limit_per_minute: int = 75
    api_key: str = ""


class SchwabSettings(BaseModel):
    base_url: str = "https://api.schwabapi.com"
    redirect_uri: str = "https://127.0.0.1:8182"
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""


class LoggingSettings(BaseModel):
    level: str = "INFO"


class Settings(BaseModel):
    """Root settings — one section per concern."""

    mongo: MongoSettings = Field(default_factory=MongoSettings)
    poller: PollerSettings = Field(default_factory=PollerSettings)
    ia_writer: IaWriterSettings = Field(default_factory=IaWriterSettings)
    alpha_vantage: AlphaVantageSettings = Field(default_factory=AlphaVantageSettings)
    schwab: SchwabSettings = Field(default_factory=SchwabSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    # Fields whose values must never appear verbatim in logs / UI.
    _SECRET_FIELDS: tuple[tuple[str, str], ...] = (
        ("alpha_vantage", "api_key"),
        ("schwab", "client_secret"),
        ("schwab", "refresh_token"),
    )

    def redacted(self) -> dict[str, Any]:
        """Return a plain-dict copy with secret fields masked."""
        data = self.model_dump()
        for section, key in self._SECRET_FIELDS:
            val = data.get(section, {}).get(key)
            if val:
                data[section][key] = _mask(val)
        return data

    def has_alpha_vantage_key(self) -> bool:
        return bool(self.alpha_vantage.api_key)

    def has_schwab_credentials(self) -> bool:
        return bool(self.schwab.client_id and self.schwab.client_secret)


# -- Helpers --------------------------------------------------------------


def _mask(val: str) -> str:
    if len(val) <= 4:
        return "***"
    return f"{val[:2]}***{val[-2:]}"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge: overlay wins; nested tables merge; scalars replace."""
    out: dict[str, Any] = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


# -- Public API -----------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache the application settings.

    ``settings.toml`` is required; ``secrets.toml`` is optional (the app
    runs fine without credentials for offline development).
    """
    if not SETTINGS_PATH.exists():
        raise FileNotFoundError(
            f"Required config file not found: {SETTINGS_PATH}. "
            "See config/secrets.toml.example and docs/plan.md § 4."
        )
    merged = _deep_merge(_load_toml(SETTINGS_PATH), _load_toml(SECRETS_PATH))
    return Settings.model_validate(merged)


def reload_settings() -> Settings:
    """Clear the cache and reload from disk. Useful from a 'reload' button."""
    get_settings.cache_clear()
    return get_settings()

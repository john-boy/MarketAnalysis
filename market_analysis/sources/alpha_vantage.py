"""Alpha Vantage HTTP adapter.

A thin, typed wrapper around the subset of the Alpha Vantage REST API
we use in Phase 1:

- :meth:`AlphaVantageClient.time_series_daily_adjusted` → OHLCV+adjusted
- :meth:`AlphaVantageClient.overview`                   → company fundamentals
- :meth:`AlphaVantageClient.etf_profile`                → ETF holdings / sectors
- :meth:`AlphaVantageClient.earnings`                   → annual + quarterly EPS
- :meth:`AlphaVantageClient.news_sentiment`             → news + sentiment scores

Design notes
------------

* **No DB access.**  Adapters are persistence-free; services are
  responsible for writing to Mongo.
* **Rate limiting.**  A tiny in-process token-bucket keeps us under the
  paid-tier budget (default 75 req/min; configurable per call).  It is
  intentionally process-local — we run one ingest process.
* **Response caching.**  Optional on-disk cache under ``.cache/av/`` keyed
  by a SHA-256 of the sorted query params.  Useful for local development
  (free replays while iterating on ingest code).  Disabled by default;
  enable with ``cache=True`` on the client or per-call.
* **Error envelopes.**  AV returns 200 OK with a ``"Note"``,
  ``"Information"``, or ``"Error Message"`` key when rate-limited or
  when a symbol is unknown.  We raise :class:`AlphaVantageError` for
  these rather than returning garbage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

import requests

from market_analysis.config import PROJECT_ROOT, get_settings


log = logging.getLogger(__name__)


DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "av"
DEFAULT_TIMEOUT = 30.0  # seconds


class AlphaVantageError(RuntimeError):
    """Raised when AV returns an error / rate-limit envelope."""


# -- Rate limiter ---------------------------------------------------------


class _RateLimiter:
    """Sliding-window limiter with an even-spacing floor.

    Two constraints enforced simultaneously:

    - at most ``max_events`` requests per ``window_sec`` (hard ceiling),
    - a minimum interval ``min_spacing_sec`` between consecutive
      requests (avoids bursting into a second-boundary wall on AV).

    Default ``min_spacing_sec`` is ``window_sec / max_events`` — i.e.
    the requests are naturally paced to just fit the window.  This
    matches the "sleep 12 s between calls on the free tier" pattern
    AV documents, generalized to any rate tier.

    Thread-safe (belt-and-suspenders — we are single-threaded today).
    """

    def __init__(
        self,
        max_events: int,
        window_sec: float = 60.0,
        min_spacing_sec: float | None = None,
    ) -> None:
        self._max = max(1, max_events)
        self._window = window_sec
        self._min_spacing = (
            min_spacing_sec if min_spacing_sec is not None
            else self._window / self._max
        )
        self._events: deque[float] = deque()
        self._last_issue: float = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - self._window
                while self._events and self._events[0] < cutoff:
                    self._events.popleft()

                wait_window = 0.0
                wait_spacing = 0.0
                if len(self._events) >= self._max:
                    wait_window = self._window - (now - self._events[0]) + 0.01
                since_last = now - self._last_issue
                if since_last < self._min_spacing:
                    wait_spacing = self._min_spacing - since_last

                wait = max(wait_window, wait_spacing)
                # Tolerance guards against FP drift: after sleeping exactly
                # min_spacing, (now - last_issue) can round to slightly less
                # than min_spacing, producing a residual wait that never
                # terminates the loop.
                if wait <= 1e-6:
                    self._events.append(now)
                    self._last_issue = now
                    return
            log.debug("AV rate-limit: sleeping %.2fs", wait)
            time.sleep(wait)


# -- Cache ----------------------------------------------------------------


def _cache_key(params: dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    # shard by first 2 hex chars to keep directories small
    return cache_dir / key[:2] / f"{key}.json"


def _cache_read(cache_dir: Path, key: str, ttl: float | None) -> Any | None:
    path = _cache_path(cache_dir, key)
    if not path.exists():
        return None
    if ttl is not None and (time.time() - path.stat().st_mtime) > ttl:
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _cache_write(cache_dir: Path, key: str, payload: Any) -> None:
    path = _cache_path(cache_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    tmp.replace(path)


# -- Client ---------------------------------------------------------------


class AlphaVantageClient:
    """Typed HTTP client for Alpha Vantage.

    Parameters
    ----------
    api_key:
        Overrides the key loaded from settings.  Useful in tests.
    base_url, rate_limit_per_minute:
        Overrides for the corresponding settings.
    session:
        An existing ``requests.Session`` to reuse (e.g. in tests).
    cache:
        Enable on-disk response cache (``.cache/av/`` by default).
    cache_dir, cache_ttl_sec:
        Cache overrides.  ``cache_ttl_sec=None`` (default) means "cache
        forever" — fine for deterministic replays; set a TTL to expire.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        rate_limit_per_minute: int | None = None,
        session: requests.Session | None = None,
        cache: bool = False,
        cache_dir: Path | None = None,
        cache_ttl_sec: float | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        s = get_settings().alpha_vantage
        self._api_key = api_key if api_key is not None else s.api_key
        self._base_url = base_url or s.base_url
        self._limiter = _RateLimiter(rate_limit_per_minute or s.rate_limit_per_minute)
        self._session = session or requests.Session()
        self._cache_enabled = cache
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._cache_ttl = cache_ttl_sec
        self._timeout = timeout

    # -- Low-level ------------------------------------------------------

    def _get(
        self,
        params: dict[str, Any],
        *,
        use_cache: bool | None = None,
    ) -> Any:
        if not self._api_key:
            raise AlphaVantageError(
                "Alpha Vantage API key not configured "
                "(config/secrets.toml → [alpha_vantage].api_key)."
            )
        # Sign
        p = {**params, "apikey": self._api_key}

        # Cache lookup (cache key excludes the apikey so rotations don't
        # invalidate everything).
        cache_enabled = self._cache_enabled if use_cache is None else use_cache
        cache_params = {k: v for k, v in p.items() if k != "apikey"}
        key = _cache_key(cache_params)
        if cache_enabled:
            hit = _cache_read(self._cache_dir, key, self._cache_ttl)
            if hit is not None:
                log.debug("AV cache HIT  %s", cache_params.get("function"))
                return hit

        # Retry loop for transient 429s (max 3 attempts).
        for attempt in range(3):
            self._limiter.acquire()
            log.debug("AV GET %s (attempt %d)", cache_params, attempt + 1)
            resp = self._session.get(self._base_url, params=p, timeout=self._timeout)
            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                log.warning(
                    "AV 429 rate-limited; sleeping %.1fs (attempt %d/3)",
                    retry_after, attempt + 1,
                )
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            break
        else:
            raise AlphaVantageError("Alpha Vantage returned 429 three times in a row.")

        payload = resp.json()
        _check_envelope(payload, cache_params)

        if cache_enabled:
            _cache_write(self._cache_dir, key, payload)
        return payload

    # -- Typed endpoints -----------------------------------------------

    def time_series_daily_adjusted(
        self,
        symbol: str,
        *,
        outputsize: str = "full",
        datatype: str = "json",
        use_cache: bool | None = None,
    ) -> dict[str, Any]:
        """``TIME_SERIES_DAILY_ADJUSTED`` — full daily OHLCV + adjusted close.

        ``outputsize='full'`` (default) returns 20+ years of history.
        """
        return self._get(
            {
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": outputsize,
                "datatype": datatype,
            },
            use_cache=use_cache,
        )

    def index_data(
        self,
        symbol: str,
        *,
        interval: str = "daily",
        use_cache: bool | None = None,
    ) -> dict[str, Any]:
        """``INDEX_DATA`` — daily/weekly/monthly OHLC for a market index.

        Returns a payload shaped like the ``TIME_SERIES_*`` endpoints but
        with a smaller field set (open/high/low/close; no volume,
        adjusted close, dividends, or splits).  Used for non-ETF indexes
        (SPX, VIX, NDX, …) where direct provider data beats a proxy.
        """
        return self._get(
            {
                "function": "INDEX_DATA",
                "symbol": symbol,
                "interval": interval,
            },
            use_cache=use_cache,
        )

    def splits(self, symbol: str, *, use_cache: bool | None = None) -> dict[str, Any]:
        """``SPLITS`` — historical split events for a symbol.

        Used by the WyckoffDB ``splits_events`` backfill (see
        ``scripts/wyckoff_backfill_splits.py``). AV returns the events
        most-recent-first under a top-level ``data`` key.
        """
        return self._get(
            {"function": "SPLITS", "symbol": symbol},
            use_cache=use_cache,
        )

    def overview(self, symbol: str, *, use_cache: bool | None = None) -> dict[str, Any]:
        """``OVERVIEW`` — company fundamentals (sector, market cap, PE, …)."""
        return self._get(
            {"function": "OVERVIEW", "symbol": symbol},
            use_cache=use_cache,
        )

    def etf_profile(self, symbol: str, *, use_cache: bool | None = None) -> dict[str, Any]:
        """``ETF_PROFILE`` — ETF holdings and sector weights."""
        return self._get(
            {"function": "ETF_PROFILE", "symbol": symbol},
            use_cache=use_cache,
        )

    def earnings(self, symbol: str, *, use_cache: bool | None = None) -> dict[str, Any]:
        """``EARNINGS`` — annual + quarterly EPS history."""
        return self._get(
            {"function": "EARNINGS", "symbol": symbol},
            use_cache=use_cache,
        )

    def news_sentiment(
        self,
        *,
        tickers: Iterable[str] | None = None,
        topics: Iterable[str] | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        sort: str = "LATEST",
        limit: int = 50,
        use_cache: bool | None = None,
    ) -> dict[str, Any]:
        """``NEWS_SENTIMENT`` — curated news + per-ticker sentiment scores.

        All parameters optional.  ``time_from``/``time_to`` are
        ``YYYYMMDDTHHMM`` per AV spec.
        """
        params: dict[str, Any] = {
            "function": "NEWS_SENTIMENT",
            "sort": sort,
            "limit": limit,
        }
        if tickers:
            params["tickers"] = ",".join(tickers)
        if topics:
            params["topics"] = ",".join(topics)
        if time_from:
            params["time_from"] = time_from
        if time_to:
            params["time_to"] = time_to
        return self._get(params, use_cache=use_cache)


# -- Envelope detection ---------------------------------------------------


_ERROR_KEYS = ("Error Message", "Information", "Note")


def _parse_retry_after(value: str | None) -> float:
    """Parse a ``Retry-After`` header; return a sensible default on miss."""
    if not value:
        return 15.0
    try:
        return max(1.0, float(value))
    except ValueError:
        return 15.0


def _check_envelope(payload: Any, params: dict[str, Any]) -> None:
    """Raise :class:`AlphaVantageError` if ``payload`` is an AV error envelope.

    AV returns 200 OK with a single top-level key (``Note``,
    ``Information``, or ``Error Message``) for rate limits, invalid
    symbols, or premium-only endpoints.  Some endpoints legitimately
    include ``Information`` alongside data — we treat it as an error
    only when it is the *only* or dominant key.
    """
    if not isinstance(payload, dict):
        return
    for k in _ERROR_KEYS:
        if k in payload and len(payload) <= 2:
            raise AlphaVantageError(
                f"Alpha Vantage returned {k!r}: {payload[k]!r} "
                f"(function={params.get('function')!r})"
            )

"""Read-only Alpaca historical acquisition and immutable partition handling."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import shutil
import time
from typing import Any, Callable, Iterable

import httpx

from app.live_scanner.session import scanner_session
from app.market.calendar import EASTERN

from .models import HistoricalBar

ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_PROVIDER = "ALPACA"
ALPACA_FREE_FEED = "IEX"
COVERAGE_CLASS = "SINGLE_EXCHANGE_FREE_RESEARCH"


@dataclass(frozen=True, slots=True)
class AcquisitionConfig:
    provider: str = ALPACA_PROVIDER
    feed: str = ALPACA_FREE_FEED
    timeframe: str = "1Min"
    start_date: date | None = None
    end_date: date | None = None
    raw_root: Path = Path("data/research/market_data/raw")
    normalized_root: Path = Path("data/research/market_data/normalized")
    manifest_root: Path = Path("data/research/market_data/manifests")
    requests_per_minute: int = 180
    max_retries: int = 4
    min_free_bytes: int = 1_000_000_000
    max_pages: int = 10_000

    def __post_init__(self) -> None:
        if self.provider.upper() != ALPACA_PROVIDER or self.feed.upper() != ALPACA_FREE_FEED:
            raise ValueError("only ALPACA/IEX free research configuration is permitted")
        if self.requests_per_minute <= 0 or self.max_retries < 0 or self.max_pages <= 0:
            raise ValueError("acquisition limits must be positive")


@dataclass(frozen=True, slots=True)
class PartitionManifest:
    provider: str
    feed: str
    coverage_class: str
    symbol: str
    trading_date: str
    timeframe: str
    request_start: str
    request_end: str
    downloaded_at: str
    row_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    content_hash: str
    normalization_version: int
    status: str
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RateLimiter:
    def __init__(self, requests_per_minute: int, *, clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.interval = 60.0 / requests_per_minute
        self.clock, self.sleeper = clock, sleeper
        self._last = None

    def wait(self) -> None:
        now = self.clock()
        if self._last is not None and now - self._last < self.interval:
            self.sleeper(self.interval - (now - self._last))
        self._last = self.clock()


def _safe_error(value: object) -> str:
    text = str(value)
    for secret in ("api_key", "api_secret", "authorization", "bearer", "token"):
        text = text.replace(secret, "[REDACTED]")
    return " ".join(text.split())[:240]


class AlpacaHistoricalClient:
    """The public surface contains historical data retrieval only."""

    def __init__(self, api_key: str, api_secret: str, *, config: AcquisitionConfig | None = None,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 random_value: Callable[[], float] = random.random) -> None:
        if not api_key.strip() or not api_secret.strip():
            raise ValueError("Alpaca historical credentials are required")
        self.config = config or AcquisitionConfig()
        self._headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
        self._client = httpx.Client(base_url=ALPACA_DATA_URL, headers=self._headers, transport=transport, timeout=20.0)
        self._limiter = RateLimiter(self.config.requests_per_minute, sleeper=sleep)
        self._sleep, self._random = sleep, random_value
        self.last_rate_headers: dict[str, str] = {}

    @classmethod
    def from_environment(cls, *, config: AcquisitionConfig | None = None, **kwargs: Any) -> "AlpacaHistoricalClient":
        import os
        return cls(os.environ.get("ALPACA_API_KEY", ""), os.environ.get("ALPACA_API_SECRET", ""), config=config, **kwargs)

    def close(self) -> None:
        self._client.close()

    def health_check(self) -> dict[str, object]:
        response = self._request("/v2/stocks/AAPL/bars", {"timeframe": "1Min", "limit": "1", "feed": self.config.feed})
        return {"authenticated": True, "feed": self.config.feed, "historical_access": True,
                "rate_limit": {key: value for key, value in self.last_rate_headers.items()
                                if key.lower().startswith("x-ratelimit")}}

    def fetch_minute_bars(self, symbol: str, start: datetime, end: datetime) -> tuple[dict[str, object], ...]:
        params = {"timeframe": self.config.timeframe, "start": start.isoformat(), "end": end.isoformat(),
                  "feed": self.config.feed, "adjustment": "raw", "limit": "10000"}
        return tuple(self._paginate(f"/v2/stocks/{symbol.upper()}/bars", params))

    def fetch_daily_bars(self, symbols: Iterable[str], start: datetime, end: datetime) -> tuple[dict[str, object], ...]:
        params = {"timeframe": "1Day", "symbols": ",".join(sorted({s.upper() for s in symbols})),
                  "start": start.isoformat(), "end": end.isoformat(), "feed": self.config.feed, "limit": "10000"}
        return tuple(self._paginate("/v2/stocks/bars", params))

    def _paginate(self, path: str, params: dict[str, object]):
        token = None; seen = set()
        for _ in range(self.config.max_pages):
            query = dict(params)
            if token is not None:
                if token in seen:
                    raise RuntimeError("PAGINATION_LOOP")
                seen.add(token); query["page_token"] = token
            payload = self._request(path, query)
            rows = payload.get("bars", payload.get("data", ()))
            if not isinstance(rows, list):
                raise ValueError("MALFORMED_RESPONSE")
            yield from rows
            token = payload.get("next_page_token")
            if not token:
                return
        raise RuntimeError("PAGINATION_LIMIT")

    def _request(self, path: str, params: dict[str, object]) -> dict[str, object]:
        for attempt in range(self.config.max_retries + 1):
            self._limiter.wait()
            try:
                response = self._client.get(path, params=params)
                self.last_rate_headers = dict(response.headers)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.config.max_retries: raise RuntimeError(f"TRANSIENT_NETWORK: {_safe_error(exc)}") from exc
                self._backoff(attempt); continue
            if response.status_code in (401, 403):
                raise PermissionError(f"ALPACA_AUTH_OR_PERMISSION_{response.status_code}")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self.config.max_retries: raise RuntimeError(f"HTTP_RETRY_EXHAUSTED_{response.status_code}")
                self._backoff(attempt, response.headers.get("Retry-After")); continue
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP_PERMANENT_{response.status_code}: {_safe_error(response.text)}")
            try:
                value = response.json()
            except ValueError as exc:
                raise ValueError("MALFORMED_RESPONSE") from exc
            if not isinstance(value, dict): raise ValueError("MALFORMED_RESPONSE")
            return value
        raise RuntimeError("REQUEST_FAILED")

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(30.0, 2 ** attempt + self._random())
        self._sleep(delay)


def normalize_bar(row: dict[str, object], *, symbol: str, trading_date: date,
                 previous_close: Decimal | None = None) -> HistoricalBar:
    timestamp = datetime.fromisoformat(str(row.get("t", row.get("timestamp"))))
    if timestamp.tzinfo is None: raise ValueError("INVALID_TIMESTAMP")
    actual_symbol = str(row.get("S", row.get("symbol", symbol))).upper()
    if actual_symbol != symbol.upper(): raise ValueError("WRONG_SYMBOL")
    # UTC timestamps around midnight may have the preceding UTC date; session
    # ownership is the NYSE-local date, not the serialized UTC date.
    if timestamp.astimezone(EASTERN).date() != trading_date: raise ValueError("WRONG_DATE")
    def value(short: str, long: str) -> object:
        result = row.get(short, row.get(long))
        if result is None: raise ValueError("MALFORMED_RESPONSE")
        return result
    return HistoricalBar(actual_symbol, timestamp, Decimal(str(value("o", "open"))),
                         Decimal(str(value("h", "high"))), Decimal(str(value("l", "low"))),
                         Decimal(str(value("c", "close"))), Decimal(str(value("v", "volume"))),
                         scanner_session(timestamp).value, ALPACA_PROVIDER, ALPACA_FREE_FEED, "UTC", 1, previous_close,
                         *(Decimal(str(row[key])) if row.get(key) is not None else None for key in ("bid", "ask", "bid_size", "ask_size")),
                         trade_count=None if row.get("n") is None else int(row["n"]),
                         provider_vwap=None if row.get("vw") is None else Decimal(str(row["vw"])))


def validate_source_bars(rows: Iterable[HistoricalBar]) -> tuple[HistoricalBar, ...]:
    ordered = sorted(rows, key=lambda item: (item.symbol, item.timestamp))
    result: list[HistoricalBar] = []; identities: dict[tuple[str, datetime], HistoricalBar] = {}
    for bar in ordered:
        key = (bar.symbol, bar.timestamp)
        prior = identities.get(key)
        if prior is not None:
            if prior != bar: raise ValueError("BAR_CONFLICT")
            continue
        identities[key] = bar; result.append(bar)
    return tuple(result)


def detect_intraday_gaps(rows: Iterable[HistoricalBar], *, expected_minutes: int = 1) -> tuple[dict[str, object], ...]:
    """Report gaps without inventing candles or treating session breaks as errors."""
    ordered = sorted(rows, key=lambda item: (item.symbol, item.timestamp))
    findings: list[dict[str, object]] = []
    for previous, current in zip(ordered, ordered[1:]):
        if previous.symbol != current.symbol:
            continue
        minutes = int((current.timestamp - previous.timestamp).total_seconds() // 60)
        if minutes > expected_minutes:
            findings.append({"symbol": current.symbol, "from": previous.timestamp.isoformat(),
                             "to": current.timestamp.isoformat(), "missing_minutes": minutes - expected_minutes,
                             "previous_session": previous.session, "current_session": current.session,
                             "classification": "REVIEW_REQUIRED"})
    return tuple(findings)


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".inprogress")
    payload = ("\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows) + "\n").encode()
    with temp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        import os
        os.fsync(handle.fileno())
    digest = hashlib.sha256(payload).hexdigest()
    temp.replace(path)
    return digest


def disk_space_ok(path: Path, minimum: int) -> bool:
    return shutil.disk_usage(path.anchor or Path.cwd()).free >= minimum


def partition_paths(config: AcquisitionConfig, symbol: str, trading_date: date) -> tuple[Path, Path, Path]:
    stem = f"{symbol.upper()}_{trading_date.isoformat()}"
    return (config.raw_root / f"{stem}.jsonl", config.normalized_root / f"{stem}.jsonl",
            config.manifest_root / f"{stem}.json")


def download_partition(client: AlpacaHistoricalClient, config: AcquisitionConfig, symbol: str,
                       trading_date: date, *, previous_close: Decimal | None = None) -> PartitionManifest:
    """Download one symbol-day atomically; callers can safely retry this unit."""
    if not disk_space_ok(config.raw_root, config.min_free_bytes):
        raise RuntimeError("DISK_SPACE_SAFETY_FLOOR")
    raw_path, normalized_path, manifest_path = partition_paths(config, symbol, trading_date)
    if raw_path.exists() and normalized_path.exists() and manifest_path.exists():
        try:
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            if saved.get("status") in {"COMPLETE", "EMPTY_CONFIRMED"}:
                digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                if digest == saved.get("content_hash"):
                    return PartitionManifest(**saved)
        except (OSError, ValueError, TypeError, KeyError):
            # A corrupt or incomplete manifest is reacquired below.
            pass
    start = datetime.combine(trading_date, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    rows = client.fetch_minute_bars(symbol, start, end)
    raw_records = tuple(dict(row) for row in rows)
    raw_hash = atomic_write_jsonl(raw_path, raw_records)
    normalized = validate_source_bars(tuple(normalize_bar(row, symbol=symbol, trading_date=trading_date,
                                                          previous_close=previous_close) for row in raw_records))
    normalized_records = ({"symbol": bar.symbol, "timestamp": bar.timestamp.isoformat(),
                           "session": bar.session, "open": str(bar.open), "high": str(bar.high),
                           "low": str(bar.low), "close": str(bar.close), "volume": str(bar.volume),
                           "provider": bar.provider, "feed": bar.feed, "source_timezone": bar.source_timezone,
                           "normalization_version": bar.normalization_version,
                           "previous_close": None if bar.previous_close is None else str(bar.previous_close),
                           "trade_count": bar.trade_count,
                           "provider_vwap": None if bar.provider_vwap is None else str(bar.provider_vwap)}
                          for bar in normalized)
    atomic_write_jsonl(normalized_path, normalized_records)
    status = "EMPTY_CONFIRMED" if not normalized else "COMPLETE"
    manifest = PartitionManifest(ALPACA_PROVIDER, ALPACA_FREE_FEED, COVERAGE_CLASS, symbol.upper(),
                                 trading_date.isoformat(), config.timeframe, start.isoformat(), end.isoformat(),
                                 datetime.now(UTC).isoformat(), len(normalized),
                                 None if not normalized else normalized[0].timestamp.isoformat(),
                                 None if not normalized else normalized[-1].timestamp.isoformat(), raw_hash, 1, status)
    atomic_write_jsonl(manifest_path, (manifest.to_dict(),))
    return manifest

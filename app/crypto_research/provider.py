"""Official Webull market-data adapter for crypto research only."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from threading import Lock
from time import monotonic, sleep

from .models import CryptoObservation, CryptoPair


class CryptoProviderError(RuntimeError):
    pass


class UnsupportedCryptoSymbolError(CryptoProviderError):
    pass


class MalformedCryptoQuoteError(CryptoProviderError):
    pass


class WebullCryptoResearchProvider:
    """Bounded snapshot/instrument access with no trading-client dependency."""

    def __init__(
        self,
        client: object,
        *,
        minimum_request_interval_seconds: float = 1.0,
        retry_attempts: int = 1,
        maximum_symbols: int = 256,
        maximum_pages: int = 10,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if minimum_request_interval_seconds < 0 or retry_attempts < 0:
            raise ValueError("provider rate/retry bounds cannot be negative")
        if maximum_symbols <= 0 or maximum_pages <= 0:
            raise ValueError("provider cardinality bounds must be positive")
        self._client = client
        self._minimum_interval = minimum_request_interval_seconds
        self._retry_attempts = retry_attempts
        self._maximum_symbols = maximum_symbols
        self._maximum_pages = maximum_pages
        self._clock = clock
        self._monotonic = monotonic_clock
        self._sleep = sleeper
        self._rate_lock = Lock()
        self._last_request_at: float | None = None

    def discover(
        self, configured: Sequence[CryptoPair] = ()
    ) -> tuple[CryptoPair, ...]:
        requested = tuple(configured)
        symbols = tuple(item.provider_symbol for item in requested)
        rows: list[Mapping[str, object]] = []
        cursor: str | None = None
        page_size = min(1000, self._maximum_symbols)
        for _ in range(self._maximum_pages):
            response = self._request(
                "instrument discovery",
                lambda: self._instrument_api().get_crypto_instrument(
                    symbols=",".join(symbols) if symbols else None,
                    category="US_CRYPTO",
                    last_instrument_id=cursor,
                    page_size=page_size,
                ),
            )
            page = _rows(response)
            rows.extend(page)
            if (
                requested
                or len(rows) >= self._maximum_symbols
                or not page
                or len(page) < page_size
            ):
                break
            next_cursor = _cursor(response, page)
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        pairs: dict[str, CryptoPair] = {}
        for row in rows[: self._maximum_symbols]:
            pair = pair_from_webull(row)
            pairs[pair.canonical_symbol] = pair
        if requested:
            supported = {item.provider_symbol for item in pairs.values()}
            missing = tuple(item.provider_symbol for item in requested if item.provider_symbol not in supported)
            if missing:
                raise UnsupportedCryptoSymbolError(
                    "unsupported configured crypto pair(s): " + ",".join(missing)
                )
        return tuple(sorted(pairs.values(), key=lambda item: item.canonical_symbol))

    def snapshots(
        self, pairs: Sequence[CryptoPair]
    ) -> tuple[CryptoObservation, ...]:
        selected = tuple(pairs)
        if len(selected) > 20:
            raise ValueError("Webull crypto snapshots support at most 20 symbols")
        if not selected:
            return ()
        response = self._request(
            "snapshot",
            lambda: self._market_api().get_crypto_snapshot(
                [item.provider_symbol for item in selected], "US_CRYPTO"
            ),
        )
        identities = {item.provider_symbol: item for item in selected}
        observations = []
        for row in _rows(response):
            symbol = str(row.get("symbol", "")).strip().upper()
            pair = identities.get(symbol)
            if pair is None:
                continue
            observations.append(observation_from_webull(pair, row, self._clock()))
        return tuple(observations)

    def historical_bars(
        self,
        pair: CryptoPair,
        *,
        timespan: str = "M1",
        count: int = 200,
        real_time_required: bool = False,
    ) -> tuple[Mapping[str, object], ...]:
        if not 1 <= count <= 1200:
            raise ValueError("crypto historical bar count must be 1..1200")
        response = self._request(
            "historical bars",
            lambda: self._market_api().get_crypto_history_bar(
                pair.provider_symbol, "US_CRYPTO", timespan, str(count), real_time_required
            ),
        )
        return _rows(response)

    def _instrument_api(self):
        return getattr(self._resolved_client(), "instrument")

    def _market_api(self):
        return getattr(self._resolved_client(), "crypto_market_data")

    def _resolved_client(self):
        getter = getattr(self._client, "get", None)
        return getter() if callable(getter) else self._client

    def _request(self, operation: str, call: Callable[[], object]) -> object:
        last_error: Exception | None = None
        for attempt in range(self._retry_attempts + 1):
            self._rate_limit()
            try:
                response = call()
                status = getattr(response, "status_code", 200)
                if status == 417:
                    raise UnsupportedCryptoSymbolError(operation + " rejected a symbol")
                if not isinstance(status, int) or status < 400:
                    return response
                if status in {401, 403}:
                    raise PermissionError(f"Webull crypto {operation} is not entitled")
                raise CryptoProviderError(f"Webull crypto {operation} failed: HTTP {status}")
            except (UnsupportedCryptoSymbolError, PermissionError):
                raise
            except Exception as exc:
                last_error = exc
                if attempt == self._retry_attempts:
                    break
        raise CryptoProviderError(f"Webull crypto {operation} failed") from last_error

    def _rate_limit(self) -> None:
        with self._rate_lock:
            now = self._monotonic()
            if self._last_request_at is not None:
                remaining = self._minimum_interval - (now - self._last_request_at)
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._monotonic()
            self._last_request_at = now


def pair_from_webull(row: Mapping[str, object]) -> CryptoPair:
    provider = str(row.get("symbol", row.get("ticker", ""))).strip().upper()
    base = _first(row, "base_asset", "base_currency", "base_coin", "coin")
    quote = _first(row, "quote_asset", "quote_currency", "currency_code", "currency")
    if not provider:
        raise ValueError("Webull crypto instrument is missing symbol")
    if base is None and quote is not None and provider.endswith(quote):
        base = provider[: -len(quote)]
    if base is None or quote is None:
        for separator in ("/", "-", ":"):
            if separator in provider:
                parts = provider.split(separator)
                if len(parts) == 2:
                    base, quote = parts
                break
    if base is None or quote is None:
        raise ValueError("Webull crypto instrument lacks explicit pair metadata")
    return CryptoPair(base, quote, provider)


def observation_from_webull(
    pair: CryptoPair, row: Mapping[str, object], adopted_at: datetime
) -> CryptoObservation:
    try:
        price = _decimal(row, "price", "close", "last_price", required=True)
        # The official crypto snapshot schema does not include traded volume.
        # Absence is research evidence, not an observed zero.
        volume = _decimal(row, "volume", "volume_24h")
        timestamp = _timestamp(
            row.get("quote_time", row.get("last_trade_time", row.get("timestamp"))),
            adopted_at,
        )
        return CryptoObservation(
            pair=pair,
            timestamp=timestamp,
            price=price,
            bid=_decimal(row, "bid", "bid_price"),
            ask=_decimal(row, "ask", "ask_price"),
            volume=volume,
            high=_decimal(row, "high", "high_24h"),
            low=_decimal(row, "low", "low_24h"),
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MalformedCryptoQuoteError(
            f"malformed crypto quote for {pair.canonical_symbol}"
        ) from exc


def _rows(response: object) -> tuple[Mapping[str, object], ...]:
    value = response.json() if callable(getattr(response, "json", None)) else response
    if isinstance(value, Mapping):
        value = next(
            (value[key] for key in ("data", "items", "instruments", "snapshots", "bars") if key in value),
            (),
        )
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise CryptoProviderError("Webull crypto response schema is unsupported")
    rows: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        # The official crypto history endpoint returns one envelope per symbol:
        # {symbol, instrument_id, result: [{time, open, high, low, close, ...}]}.
        # Snapshot/instrument endpoints return ordinary row mappings, so only a
        # list-valued result is expanded here.
        result = item.get("result")
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
            rows.extend(entry for entry in result if isinstance(entry, Mapping))
        else:
            rows.append(item)
    return tuple(rows)


def _cursor(response: object, rows: Sequence[Mapping[str, object]]) -> str | None:
    value = response.json() if callable(getattr(response, "json", None)) else response
    if isinstance(value, Mapping):
        direct = _first(value, "last_instrument_id", "next_cursor")
        if direct:
            return direct
    return _first(rows[-1], "instrument_id") if rows else None


def _first(row: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().upper()
    return None


def _decimal(
    row: Mapping[str, object], *keys: str, required: bool = False
) -> Decimal | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return Decimal(str(value))
    if required:
        raise ValueError(f"missing required crypto field: {keys[0]}")
    return None


def _timestamp(value: object, adopted_at: datetime) -> datetime:
    if value is None:
        return adopted_at
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float, Decimal)) or str(value).isdigit():
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = [
    "CryptoProviderError",
    "MalformedCryptoQuoteError",
    "UnsupportedCryptoSymbolError",
    "WebullCryptoResearchProvider",
    "observation_from_webull",
    "pair_from_webull",
]

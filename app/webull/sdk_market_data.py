"""Official Webull SDK providers for scanner discovery and reference data."""

from __future__ import annotations

import logging
import atexit
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from threading import RLock
from typing import Any
from urllib.parse import urlparse

from app.catalysts import CatalystAggregator, WebullCatalystProvider
from app.live_scanner.session import ScannerSession, scanner_session
from app.momentum_scanner import AssetClass, CatalystStatus, CatalystType
from app.momentum_scanner.models import FloatProvenance
from app.reference_data.models import ReferenceRecord
from app.reference_data.provider import UnsupportedReferenceSymbolError
from app.universe.models import SecurityType, UniverseSymbol


class WebullMarketDataPermissionError(PermissionError):
    """The configured application lacks Webull OpenAPI market-data access."""


class _UnsupportedSymbolResponse(RuntimeError):
    error_code = "UNSUPPORTED_SYMBOL"
    http_status = 417


_SDK_LOGGER_NAME = "webull.core"
_ATLAS_LOGGER = logging.getLogger("atlas.webull.market_data")
_SDK_LOG_LOCK = RLock()
_SDK_LOGGERS = ("webull", "webull.core", "webull.data")
_SDK_LOGGING_CONFIGURED = False


def configure_official_sdk_logging() -> logging.Logger:
    """Disable SDK request dumps with one process-owned handler per logger."""

    global _SDK_LOGGING_CONFIGURED
    with _SDK_LOG_LOCK:
        for name in _SDK_LOGGERS:
            logger = logging.getLogger(name)
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            logger.setLevel(logging.CRITICAL)
        if not _SDK_LOGGING_CONFIGURED:
            atexit.register(shutdown_official_sdk_logging)
            _SDK_LOGGING_CONFIGURED = True
        return logging.getLogger(_SDK_LOGGER_NAME)


def shutdown_official_sdk_logging() -> None:
    """Close Atlas-owned SDK handlers deterministically during shutdown."""

    with _SDK_LOG_LOCK:
        for name in _SDK_LOGGERS:
            logger = logging.getLogger(name)
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


def create_official_data_client(
    *,
    app_key: str,
    app_secret: str,
    endpoint: str,
    region_id: str = "us",
) -> object:
    """Create the official SDK DataClient lazily at scanner startup."""

    api_client = build_official_data_api_client(
        app_key=app_key,
        app_secret=app_secret,
        endpoint=endpoint,
        region_id=region_id,
    )

    from webull.data.data_client import DataClient

    try:
        return DataClient(api_client)
    except Exception as exc:
        if _permission_failure(exc):
            raise WebullMarketDataPermissionError(
                "Webull OpenAPI market-data permission is unavailable"
            ) from exc
        raise


def build_official_data_api_client(
    *,
    app_key: str,
    app_secret: str,
    endpoint: str,
    region_id: str = "us",
) -> object:
    """Build Atlas's configured SDK client without initializing DataClient."""

    if not app_key.strip() or not app_secret.strip():
        raise WebullMarketDataPermissionError(
            "Webull OpenAPI market-data credentials are required"
        )
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise ValueError("Webull market-data endpoint must be a secure host")

    try:
        from webull.core.client import ApiClient
    except ImportError as exc:
        raise RuntimeError(
            "Webull OpenAPI SDK is unavailable; install "
            "webull-openapi-python-sdk"
        ) from exc

    api_client = ApiClient(
        app_key=app_key,
        app_secret=app_secret,
        region_id=region_id,
        port=parsed.port or 443,
        auto_retry=False,
        max_retry_num=0,
    )
    api_client.add_endpoint(region_id, parsed.hostname)
    api_client.append_user_agent("Atlas", "1.0")
    api_client.set_logger(configure_official_sdk_logging())
    # DataClient otherwise installs stdout and rotating-file handlers which
    # include the complete signed request on an API failure.
    api_client._stream_logger_set = True
    api_client._file_logger_set = True
    return api_client


class LazyOfficialDataClient:
    def __init__(self, factory: Callable[[], object]) -> None:
        if not callable(factory):
            raise TypeError("data client factory must be callable")
        self._factory = factory
        self._client: object | None = None
        self._lock = RLock()

    def get(self) -> object:
        with self._lock:
            if self._client is None:
                self._client = self._factory()
            return self._client


class EnvironmentSupportCache:
    """TTL cache isolated by environment, category, and canonical API symbol."""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(hours=6),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        identity_scope: str = "fp_legacy",
    ) -> None:
        if ttl <= timedelta():
            raise ValueError("support cache ttl must be positive")
        self._ttl = ttl
        self._clock = clock
        self._fingerprint = identity_scope
        self._entries: dict[tuple[str, str, str, str], tuple[bool, datetime]] = {}
        self._lock = RLock()

    def get(
        self,
        environment: str,
        category: str,
        api_symbol: str,
        *,
        identity_scope: str | None = None,
    ) -> bool | None:
        key = _support_key(
            environment, identity_scope or self._fingerprint, category, api_symbol
        )
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            supported, expires_at = entry
            if self._clock() >= expires_at:
                self._entries.pop(key, None)
                return None
            return supported

    def put(
        self,
        environment: str,
        category: str,
        api_symbol: str,
        supported: bool,
        *,
        identity_scope: str | None = None,
    ) -> None:
        key = _support_key(
            environment, identity_scope or self._fingerprint, category, api_symbol
        )
        with self._lock:
            self._entries[key] = (supported, self._clock() + self._ttl)

    def invalidate(
        self,
        environment: str,
        category: str,
        api_symbol: str,
        *,
        identity_scope: str | None = None,
    ) -> None:
        with self._lock:
            self._entries.pop(
                _support_key(
                    environment,
                    identity_scope or self._fingerprint,
                    category,
                    api_symbol,
                ),
                None,
            )


class WebullScannerUniverseProvider:
    """Discover an autonomous scanner seed set through Webull screeners."""

    def __init__(
        self,
        client: LazyOfficialDataClient,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        page_size: int = 50,
    ) -> None:
        if page_size < 1 or page_size > 100:
            raise ValueError("scanner screener page_size must be 1..100")
        self._client = client
        self._clock = clock
        self._page_size = page_size
        self._rows: dict[str, Mapping[str, object]] = {}
        self._instruments: dict[str, UniverseSymbol] = {}

    def list_symbols(
        self,
        asset_class: AssetClass,
    ) -> tuple[UniverseSymbol, ...]:
        if asset_class is not AssetClass.STOCK:
            return ()

        client = self._client.get()
        screener = getattr(client, "screener")
        session = scanner_session(self._clock())
        rank_type = {
            ScannerSession.PREMARKET: "PRE_MARKET",
            ScannerSession.AFTER_HOURS: "AFTER_MARKET",
        }.get(session, "DAY_1")

        responses = (
            screener.get_gainers_losers(
                rank_type,
                "US_STOCK",
                "CHANGE_RATIO",
                page_index=1,
                page_size=self._page_size,
                direction="DESC",
            ),
            screener.get_most_active(
                "US_STOCK",
                sort_by="RELATIVE_VOLUME_10D",
                page_index=1,
                page_size=self._page_size,
                direction="DESC",
            ),
        )
        rows: dict[str, Mapping[str, object]] = {}
        for response in responses:
            for row in _response_rows(response):
                symbol = str(row.get("symbol", "")).strip().upper()
                if symbol:
                    rows[symbol] = row
        self._rows = rows
        instrument_rows = _instrument_rows(client, tuple(sorted(rows)))
        instruments = tuple(
            item
            for symbol in sorted(rows)
            if (
                item := _universe_symbol(
                    rows[symbol],
                    instrument_rows.get(symbol),
                )
            )
            is not None
        )
        self._instruments = {item.display_symbol: item for item in instruments}
        return instruments

    def row_for(self, symbol: str) -> Mapping[str, object] | None:
        return self._rows.get(symbol.strip().upper())

    def instrument_for(self, symbol: str) -> UniverseSymbol | None:
        return self._instruments.get(symbol.strip().upper())


class WebullScannerReferenceProvider:
    """Load scanner reference facts without manufacturing missing evidence."""

    def __init__(
        self,
        client: LazyOfficialDataClient,
        universe_provider: WebullScannerUniverseProvider,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        environment: str = "UNKNOWN",
        identity_scope: str = "fp_legacy",
        support_cache: EnvironmentSupportCache | None = None,
        event_sink: Callable[[Mapping[str, object]], None] | None = None,
        catalyst_aggregator: CatalystAggregator | None = None,
    ) -> None:
        self._client = client
        self._universe = universe_provider
        self._clock = clock
        self._environment = environment.strip().upper()
        self._identity_scope = identity_scope
        self._support_cache = (
            support_cache
            if support_cache is not None
            else EnvironmentSupportCache(
                clock=clock, identity_scope=identity_scope
            )
        )
        self._event_sink = event_sink
        self._catalyst_aggregator = (
            catalyst_aggregator
            if catalyst_aggregator is not None
            else CatalystAggregator(
                (WebullCatalystProvider(client),),
                clock=clock,
            )
        )

    def get_reference_data(
        self,
        symbol: str,
        asset_class: AssetClass,
    ) -> ReferenceRecord:
        if asset_class is not AssetClass.STOCK:
            raise LookupError("production scanner supports US stocks only")
        normalized = symbol.strip().upper()
        instrument = self._universe.instrument_for(normalized)
        if instrument is None:
            raise LookupError(f"scanner instrument not found: {normalized}")
        return self.get_reference_data_for_instrument(instrument)

    def get_reference_data_for_instrument(
        self,
        instrument: UniverseSymbol,
        *,
        force_validation_refresh: bool = False,
    ) -> ReferenceRecord:
        if instrument.asset_class is not AssetClass.STOCK:
            raise LookupError("production scanner supports US stocks only")
        normalized = instrument.display_symbol
        row = self._universe.row_for(normalized)
        if row is None:
            raise LookupError(f"scanner reference row not found: {normalized}")

        price = _positive(row, "price", "close")
        previous_close = _positive(row, "pre_close")
        average_volume = self._average_30_day_volume(
            instrument,
            force_validation_refresh=force_validation_refresh,
        )
        market_cap = _optional_positive(row, "market_value")
        shares_upper_bound = (
            market_cap / price if market_cap is not None else None
        )
        catalyst_result = self._catalyst_aggregator.aggregate_result(
            normalized, self._clock()
        )
        selected = catalyst_result.selected
        catalyst, headline, catalyst_status = selected.as_scanner_fields()
        selected_event = next(
            (
                event
                for event in catalyst_result.events
                if event.identity == selected.event_identity
            ),
            None,
        )

        return ReferenceRecord(
            symbol=normalized,
            asset_class=AssetClass.STOCK,
            exchange=instrument.exchange,
            previous_close=previous_close,
            average_30_day_volume=average_volume,
            # Market value / price is an outstanding-share upper bound on
            # float. Passing the low-float rule with this value is conservative.
            float_shares=shares_upper_bound,
            market_cap=market_cap,
            shares_outstanding=shares_upper_bound,
            tradable=instrument.tradable,
            float_provenance=FloatProvenance.MARKET_CAP_PRICE_PROXY,
            catalyst=catalyst,
            catalyst_headline=headline,
            catalyst_status=catalyst_status,
            as_of=self._clock(),
            current_volume=_positive(row, "volume"),
            catalyst_source=selected.source,
            catalyst_published_at=selected.published_at,
            catalyst_source_url=selected.source_url,
            corroborating_sources=(
                selected_event.sources if selected_event is not None else ()
            ),
            catalyst_evidence_count=len(catalyst_result.evidence),
            catalyst_event_count=len(catalyst_result.events),
        )

    def _average_30_day_volume(
        self,
        instrument: UniverseSymbol,
        *,
        force_validation_refresh: bool,
    ) -> Decimal:
        market_data = getattr(self._client.get(), "market_data")
        api_symbol = instrument.api_symbol or instrument.display_symbol
        category = instrument.category or "US_STOCK"
        cached = None if force_validation_refresh else self._support_cache.get(
            self._environment,
            category,
            api_symbol,
            identity_scope=self._identity_scope,
        )
        if cached is False:
            raise UnsupportedReferenceSymbolError(
                instrument.display_symbol,
                environment=self._environment,
            )
        if cached is None:
            try:
                _response_rows(
                    market_data.get_history_bar(
                        api_symbol,
                        category,
                        "D",
                        count="1",
                        real_time_required=False,
                    )
                )
            except Exception as exc:
                if _unsupported_symbol_failure(exc):
                    self._support_cache.put(
                        self._environment,
                        category,
                        api_symbol,
                        False,
                        identity_scope=self._identity_scope,
                    )
                    self._emit_rejection(instrument)
                    raise UnsupportedReferenceSymbolError(
                        instrument.display_symbol,
                        environment=self._environment,
                    ) from exc
                raise
            self._support_cache.put(
                self._environment,
                category,
                api_symbol,
                True,
                identity_scope=self._identity_scope,
            )

        try:
            rows = _response_rows(
                market_data.get_history_bar(
                    api_symbol,
                    category,
                    "D",
                    count="30",
                    real_time_required=False,
                )
            )
        except Exception as exc:
            if _unsupported_symbol_failure(exc):
                self._support_cache.put(
                    self._environment,
                    category,
                    api_symbol,
                    False,
                    identity_scope=self._identity_scope,
                )
                self._emit_rejection(instrument)
                raise UnsupportedReferenceSymbolError(
                    instrument.display_symbol,
                    environment=self._environment,
                ) from exc
            raise
        volumes = tuple(
            value
            for row in rows
            if (value := _optional_positive(row, "volume")) is not None
        )
        if not volumes:
            raise ValueError(
                "30-day volume history unavailable for "
                f"{instrument.display_symbol}"
            )
        return sum(volumes, Decimal("0")) / Decimal(len(volumes))

    def _emit_rejection(self, instrument: UniverseSymbol) -> None:
        event = {
            "event_type": "symbol_rejected",
            "symbol": instrument.display_symbol,
            "api_symbol": instrument.api_symbol,
            "reason": "unsupported_symbol",
            "stage": "reference_warmup",
            "environment": self._environment,
            "endpoint": "stock_bars",
        }
        if self._event_sink is not None:
            self._event_sink(event)
        else:
            _ATLAS_LOGGER.warning(
                "symbol_rejected symbol=%s reason=unsupported_symbol "
                "stage=reference_warmup environment=%s endpoint=stock_bars",
                instrument.display_symbol,
                self._environment,
            )

    def _catalyst(
        self,
        symbol: str,
    ) -> tuple[CatalystType, str | None, CatalystStatus]:
        return self._catalyst_aggregator.get_evidence(
            symbol,
            self._clock(),
        ).as_scanner_fields()


def _response_rows(response: object) -> tuple[Mapping[str, object], ...]:
    value = _response_value(response)
    if isinstance(value, Mapping):
        value = value.get("data", value.get("items", ()))
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("Webull market-data response did not contain rows")
    return tuple(row for row in value if isinstance(row, Mapping))


def _catalyst_response_rows(
    response: object,
    *,
    containers: tuple[str, ...],
) -> tuple[tuple[Mapping[str, object], ...], bool]:
    """Return catalyst rows and whether the reachable schema is understood."""

    value = _response_value(response)
    if isinstance(value, Mapping):
        selected = next((key for key in containers if key in value), None)
        if selected is None:
            return (), False
        value = value[selected]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return (), False
    rows = tuple(row for row in value if isinstance(row, Mapping))
    return rows, len(rows) == len(value)


def _response_value(response: object) -> object:
    status = getattr(response, "status_code", 200)
    if status in (401, 403):
        raise WebullMarketDataPermissionError(
            "Webull OpenAPI market-data permission is unavailable"
        )
    if isinstance(status, int) and status >= 400:
        body = (
            response.json()
            if callable(getattr(response, "json", None))
            else None
        )
        code = (
            str(body.get("error_code", "")).upper()
            if isinstance(body, Mapping)
            else ""
        )
        if status == 417 and code == "UNSUPPORTED_SYMBOL":
            raise _UnsupportedSymbolResponse("input symbol invalid")
        raise RuntimeError(f"Webull market-data request failed: HTTP {status}")
    return response.json() if callable(getattr(response, "json", None)) else response


def _universe_symbol(
    row: Mapping[str, object],
    instrument_row: Mapping[str, object] | None = None,
) -> UniverseSymbol | None:
    try:
        identity = instrument_row or row
        volume = _positive(row, "volume")
        relative_volume = _positive(row, "relative_volume_10d")
        return UniverseSymbol(
            symbol=str(row["symbol"]),
            api_symbol=str(identity.get("symbol", row["symbol"])),
            instrument_id=_optional_text(
                identity.get("instrument_id", row.get("instrument_id"))
            ),
            category=str(identity.get("category", "US_STOCK")),
            asset_class=AssetClass.STOCK,
            exchange=_exchange(
                identity.get("exchange_code", row.get("exchange_code"))
            ),
            security_type=SecurityType.COMMON_STOCK,
            tradable=_instrument_is_tradable(identity),
            halted=_instrument_is_halted(identity),
            tradable_status=_optional_text(identity.get("status")),
            source="WEBULL_SCREENER",
            region="us",
            price=_positive(row, "price", "close"),
            average_30_day_volume=volume / relative_volume,
            quote_currency=str(
                row.get("currency_code", row.get("currency", "USD"))
            ),
        )
    except (KeyError, ValueError):
        return None


def _instrument_rows(
    client: object,
    symbols: tuple[str, ...],
) -> dict[str, Mapping[str, object]]:
    instrument_api = getattr(client, "instrument", None)
    lookup = getattr(instrument_api, "get_instrument", None)
    if not callable(lookup) or not symbols:
        return {}
    try:
        rows = _response_rows(
            lookup(
                symbols=",".join(symbols),
                category="US_STOCK",
                page_size=max(100, len(symbols)),
            )
        )
    except Exception:
        return {}
    return {
        str(row.get("symbol", "")).strip().upper(): row
        for row in rows
        if str(row.get("symbol", "")).strip()
    }


def _instrument_is_tradable(row: Mapping[str, object]) -> bool:
    explicit = next(
        (
            row.get(key)
            for key in ("tradable", "tradeable", "is_tradable")
            if row.get(key) is not None
        ),
        None,
    )
    if explicit is not None:
        return _as_bool(explicit)
    status = str(row.get("status", "")).strip().upper()
    return status not in {
        "INACTIVE",
        "HALTED",
        "SUSPENDED",
        "DELISTED",
        "2",
        "3",
    }


def _instrument_is_halted(row: Mapping[str, object]) -> bool:
    return str(row.get("status", "")).strip().upper() in {
        "HALTED",
        "SUSPENDED",
        "3",
    }


def _exchange(value: object) -> str:
    return {
        "NSQ": "NASDAQ",
        "NAS": "NASDAQ",
        "NMS": "NASDAQ",
        "NGM": "NASDAQ",
        "NCM": "NASDAQ",
        "NYSE": "NYSE",
        "NYQ": "NYSE",
        "ASE": "AMEX",
        "AMEX": "AMEX",
    }.get(str(value or "").strip().upper(), str(value or "UNKNOWN").upper())


def _positive(row: Mapping[str, object], *keys: str) -> Decimal:
    value = _optional_positive(row, *keys)
    if value is None:
        raise ValueError(f"missing positive Webull field: {keys[0]}")
    return value


def _optional_positive(
    row: Mapping[str, object],
    *keys: str,
) -> Decimal | None:
    value = next((row.get(key) for key in keys if row.get(key) is not None), None)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and result > 0 else None


def _recent_row(
    rows: Sequence[Mapping[str, object]],
    now: datetime,
    *,
    days: int,
) -> Mapping[str, object] | None:
    for row in reversed(rows):
        parsed = _row_date(row)
        if parsed is not None and abs((parsed - now.date()).days) <= days:
            return row
    return None


def _row_date(row: Mapping[str, object]) -> date | None:
    for key in (
        "expected_publish_date",
        "report_date",
        "earnings_date",
        "publish_date",
        "filing_date",
        "filed_date",
        "accepted_time",
        "date",
    ):
        parsed = _date(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _headline(row: Mapping[str, object], fallback: str) -> str:
    for key in ("headline", "title", "form_type", "event_type"):
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return fallback


def _permission_failure(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".upper()
    return any(token in text for token in ("403", "FORBIDDEN", "PERMISSION"))


def _unsupported_symbol_failure(exc: Exception) -> bool:
    code = str(getattr(exc, "error_code", "")).upper()
    status = getattr(exc, "http_status", None)
    text = f"{type(exc).__name__}: {exc}".upper()
    return (
        code == "UNSUPPORTED_SYMBOL"
        or "UNSUPPORTED_SYMBOL" in text
        or (status == 417 and "SYMBOL" in text)
    )


def _support_key(
    environment: str,
    fingerprint: str,
    category: str,
    api_symbol: str,
) -> tuple[str, str, str, str]:
    return (
        environment.strip().upper(),
        fingerprint.strip().lower(),
        category.strip().upper(),
        api_symbol.strip().upper(),
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


__all__ = [
    "LazyOfficialDataClient",
    "EnvironmentSupportCache",
    "WebullMarketDataPermissionError",
    "WebullScannerReferenceProvider",
    "WebullScannerUniverseProvider",
    "build_official_data_api_client",
    "create_official_data_client",
    "configure_official_sdk_logging",
    "shutdown_official_sdk_logging",
]

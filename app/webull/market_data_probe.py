"""Independent startup capability probe for Webull market data."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import logging
import os
import re
import traceback
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from app.configuration.models import MarketDataConfiguration
from app.webull.credential_identity import credential_fingerprint
from app.webull.market_data_session import (
    Clock,
    MarketDataSession,
    current_market_data_session,
    next_premarket_start,
    utc_now,
)
from app.webull.sdk_market_data import (
    LazyOfficialDataClient,
    _permission_failure,
    _response_rows,
    _unsupported_symbol_failure,
)


PROBE_SYMBOLS = ("AAPL", "SPY", "TSLA", "MSFT", "NVDA")
SANDBOX_REQUIRED_SYMBOLS = ("AAPL",)
SANDBOX_OPTIONAL_SYMBOLS = ("SPY", "TSLA", "MSFT", "NVDA")
DEBUG_PROBE_ENVIRONMENT_VARIABLE = "ATLAS_DEBUG_MARKET_DATA_PROBE"

_LOG = logging.getLogger(__name__)
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(authorization|api[-_ ]?key|app[-_ ]?key|api[-_ ]?secret|"
    r"app[-_ ]?secret|client[-_ ]?secret|private[-_ ]?key|secret|"
    r"access[-_ ]?token|refresh[-_ ]?token|oauth[-_ ]?token|token|password|"
    r"cookie|(?:request[-_ ]?|x[-_])?signature(?:[-_ ]?nonce)?|"
    r"signed[-_ ]?headers?)"
    r"(\s*[:=]\s*|\s+)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_AUTHORIZATION_VALUE = re.compile(
    r"(?i)\bauthorization(\s*[:=]\s*|\s+)[^\r\n,;]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_URL = re.compile(r"(?i)\bhttps?://[^\s\]\[(){}<>\"']+")


class ProbeState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_ENTITLED = "NOT_ENTITLED"
    CREDENTIALS_MISSING = "CREDENTIALS_MISSING"
    NOT_TESTED = "NOT_TESTED"


class SymbolProbeState(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    NO_ENTITLEMENT = "NO_ENTITLEMENT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    state: ProbeState
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.state is ProbeState.AVAILABLE


@dataclass(frozen=True, slots=True)
class SymbolCapabilityResult:
    symbol: str
    bars: CapabilityStatus
    quote: CapabilityStatus
    snapshot: CapabilityStatus
    reference: CapabilityStatus
    streaming_subscription: CapabilityStatus
    result: SymbolProbeState


@dataclass(frozen=True, slots=True)
class MarketDataProbeResult:
    environment: str
    credential_fingerprint: str
    endpoint: CapabilityStatus
    credentials: CapabilityStatus
    bars: CapabilityStatus
    quotes: CapabilityStatus
    snapshots: CapabilityStatus
    streaming: CapabilityStatus
    subscription: CapabilityStatus
    heartbeat: CapabilityStatus
    reconnect: CapabilityStatus
    entitlement: CapabilityStatus
    reference: CapabilityStatus
    symbol_results: tuple[SymbolCapabilityResult, ...]
    probe_symbols: tuple[str, ...] = PROBE_SYMBOLS
    current_session: MarketDataSession = MarketDataSession.CLOSED
    next_retry_at: datetime | None = None
    avoided_subscription_requests: int = 0
    cached: bool = False

    @property
    def transport_connected(self) -> bool:
        return self.streaming.available

    @property
    def subscription_accepted(self) -> bool:
        return self.subscription.available

    @property
    def current_session_entitled(self) -> bool:
        return self.entitlement.available

    @property
    def entitlement_code(self) -> str | None:
        if (
            self.current_session is MarketDataSession.OVERNIGHT
            and self.entitlement.state is ProbeState.NOT_ENTITLED
            and self.subscription.state is ProbeState.NOT_ENTITLED
        ):
            return "OVERNIGHT_ENTITLEMENT_REQUIRED"
        return None

    @property
    def probe_symbol_supported(self) -> bool:
        required = (
            SANDBOX_REQUIRED_SYMBOLS
            if self.environment in {"TEST", "PAPER", "SANDBOX"}
            else PROBE_SYMBOLS
        )
        results = tuple(
            item for item in self.symbol_results if item.symbol in required
        )
        return bool(results) and all(
            all((
                item.bars.available,
                item.quote.available,
                item.snapshot.available,
                item.reference.available,
            ))
            for item in results
        )

    @property
    def bars_available(self) -> bool:
        return self.bars.available

    @property
    def quotes_available(self) -> bool:
        return self.quotes.available

    @property
    def snapshot_available(self) -> bool:
        return self.snapshots.available

    @property
    def scanner_ready(self) -> bool:
        return all((
            self.endpoint.available,
            self.credentials.available,
            self.bars.available,
            self.quotes.available,
            self.snapshots.available,
            self.streaming.available,
            self.subscription.available,
            self.heartbeat.available,
            self.reconnect.available,
            self.entitlement.available,
            self.reference.available,
        ))

    @property
    def reason(self) -> str | None:
        if self.scanner_ready:
            return None
        if self.credentials.state is ProbeState.CREDENTIALS_MISSING:
            return "Production market-data credentials are missing."
        if self.entitlement.state is ProbeState.NOT_ENTITLED:
            if (
                self.current_session is MarketDataSession.OVERNIGHT
                and self.subscription.state is ProbeState.NOT_ENTITLED
            ):
                return "OVERNIGHT_ENTITLEMENT_REQUIRED"
            label = (
                "Production"
                if self.environment in {"LIVE", "PRODUCTION"}
                else "Sandbox"
            )
            return f"{label} market-data entitlement is not granted."
        if (
            self.streaming.available
            and self.subscription.state is ProbeState.UNAVAILABLE
        ):
            return "STREAM_CONNECTED_SUBSCRIPTION_DENIED"
        if (
            self.environment in {"TEST", "PAPER", "SANDBOX"}
            and not self.probe_symbol_supported
        ):
            return "NO_SUPPORTED_SYMBOLS"
        if self.bars.state is ProbeState.UNSUPPORTED:
            if self.environment in {"TEST", "PAPER", "SANDBOX"}:
                return "Sandbox market-data catalog does not contain scanner-compatible symbols."
            return "Production market-data bars do not support the probe symbols."
        return "Market-data startup capability probe failed."


class MarketDataCapabilityProbe:
    def __init__(
        self,
        configuration: MarketDataConfiguration,
        client: LazyOfficialDataClient,
        stream: object,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._configuration = configuration
        self._client = client
        self._stream = stream
        self._clock = clock
        self._blocked_result: MarketDataProbeResult | None = None

    def configuration_changed(self) -> None:
        """Allow an explicit operator configuration change to re-run the probe."""

        self._blocked_result = None

    def run(self) -> MarketDataProbeResult:
        cfg = self._configuration
        debug = (
            os.getenv(DEBUG_PROBE_ENVIRONMENT_VARIABLE, "").strip().lower()
            == "true"
        )
        error_detail = (
            lambda exc: _debug_error(exc, (cfg.api_key, cfg.api_secret))
        ) if debug else _safe_error
        session = current_market_data_session(self._clock)
        if (
            self._blocked_result is not None
            and session is self._blocked_result.current_session
            and session is MarketDataSession.OVERNIGHT
        ):
            return replace(self._blocked_result, cached=True)
        fingerprint = credential_fingerprint(cfg.api_key, cfg.api_secret)
        missing = not cfg.api_key.strip() or not cfg.api_secret.strip()
        if missing:
            missing_status = CapabilityStatus(
                ProbeState.CREDENTIALS_MISSING,
                "market-data app key and secret are required",
            )
            not_tested = CapabilityStatus(ProbeState.NOT_TESTED)
            return MarketDataProbeResult(
                cfg.environment.value, fingerprint,
                not_tested, missing_status, not_tested, not_tested,
                not_tested, not_tested, not_tested, not_tested, not_tested,
                not_tested, not_tested, (),
                current_session=session,
            )

        try:
            client = self._client.get()
        except Exception as exc:
            denied = _permission_failure(exc)
            status = CapabilityStatus(
                ProbeState.NOT_ENTITLED if denied else ProbeState.UNAVAILABLE,
                error_detail(exc),
            )
            not_tested = CapabilityStatus(ProbeState.NOT_TESTED)
            return MarketDataProbeResult(
                cfg.environment.value, fingerprint, status, status,
                not_tested, not_tested, not_tested, not_tested, not_tested,
                not_tested, not_tested, status if denied else not_tested,
                not_tested, (),
                current_session=session,
            )

        market_data = getattr(client, "market_data")
        instrument = getattr(client, "instrument")
        stream_status = _call(self._stream.connect, error_detail)
        heartbeat = (
            _reported_capability(
                self._stream, "heartbeat_ok", "stream heartbeat unavailable",
                error_detail,
            )
            if stream_status.available else CapabilityStatus(ProbeState.NOT_TESTED)
        )
        reconnect = (
            _reported_capability(
                self._stream, "reconnect_ready", "stream reconnect unavailable",
                error_detail,
            )
            if stream_status.available else CapabilityStatus(ProbeState.NOT_TESTED)
        )
        symbol_results = []
        product_entitlement_denied = False
        avoided_subscription_requests = 0
        sandbox = cfg.environment.value in {"TEST", "PAPER", "SANDBOX"}
        required_symbols = SANDBOX_REQUIRED_SYMBOLS if sandbox else PROBE_SYMBOLS
        for symbol in PROBE_SYMBOLS:
            bar = _probe_one(lambda: market_data.get_history_bar(
                symbol, "US_STOCK", "D", count="1", real_time_required=False
            ), error_detail)
            quote = _probe_one(
                lambda: market_data.get_quotes(symbol, "US_STOCK"), error_detail
            )
            snapshot = _probe_one(
                lambda: market_data.get_snapshot(
                    symbols=[symbol], category="US_STOCK"
                ),
                error_detail,
            )
            reference_status = _probe_one(lambda: instrument.get_instrument(
                symbols=symbol, category="US_STOCK", page_size=1
            ), error_detail)
            if not stream_status.available:
                subscription_status = CapabilityStatus(ProbeState.NOT_TESTED)
            elif product_entitlement_denied:
                subscription_status = CapabilityStatus(
                    ProbeState.NOT_TESTED,
                    "skipped after US_STOCK OVERNIGHT entitlement denial",
                )
                avoided_subscription_requests += 1
            else:
                subscription_status = _probe_subscription(
                    self._stream, symbol, error_detail
                )
                product_entitlement_denied = (
                    subscription_status.detail
                    == "OVERNIGHT_ENTITLEMENT_REQUIRED"
                )
            capabilities = (bar, quote, snapshot, reference_status, subscription_status)
            symbol_results.append(SymbolCapabilityResult(
                symbol, bar, quote, snapshot, reference_status,
                subscription_status, _symbol_state(capabilities),
            ))

        required = tuple(
            item for item in symbol_results if item.symbol in required_symbols
        )
        bars = _aggregate(tuple(item.bars for item in required))
        quotes = _aggregate(tuple(item.quote for item in required))
        snapshots = _aggregate(tuple(item.snapshot for item in required))
        reference = _aggregate(tuple(item.reference for item in required))
        subscription = _aggregate(
            tuple(item.streaming_subscription for item in required)
        )
        statuses = (
            bars, quotes, snapshots, reference, stream_status, subscription,
            heartbeat, reconnect,
        )
        entitlement = (
            CapabilityStatus(ProbeState.NOT_ENTITLED, "market-data permission denied")
            if any(item.state is ProbeState.NOT_ENTITLED for item in statuses)
            else CapabilityStatus(ProbeState.AVAILABLE)
        )
        if entitlement.state is ProbeState.NOT_ENTITLED:
            symbol_results = [
                SymbolCapabilityResult(
                    item.symbol,
                    item.bars,
                    item.quote,
                    item.snapshot,
                    item.reference,
                    item.streaming_subscription,
                    SymbolProbeState.NO_ENTITLEMENT,
                )
                for item in symbol_results
            ]
        result = MarketDataProbeResult(
            cfg.environment.value,
            fingerprint,
            CapabilityStatus(ProbeState.AVAILABLE),
            CapabilityStatus(ProbeState.AVAILABLE),
            bars, quotes, snapshots, stream_status, subscription,
            heartbeat, reconnect, entitlement, reference, tuple(symbol_results),
            current_session=session,
            next_retry_at=(
                next_premarket_start(self._clock)
                if product_entitlement_denied else None
            ),
            avoided_subscription_requests=avoided_subscription_requests,
        )
        if product_entitlement_denied:
            self._blocked_result = result
        else:
            self._blocked_result = None
        return result


def _safe_error(exc: Exception) -> str:
    # Exception text from third-party clients can contain signed request data.
    return type(exc).__name__


def _probe_symbols(operation: Callable[[str], object]) -> CapabilityStatus:
    unsupported = 0
    failures = []
    for symbol in PROBE_SYMBOLS:
        try:
            _response_rows(operation(symbol))
            return CapabilityStatus(ProbeState.AVAILABLE)
        except Exception as exc:
            if _permission_failure(exc):
                return CapabilityStatus(ProbeState.NOT_ENTITLED, _safe_error(exc))
            if _unsupported_symbol_failure(exc):
                unsupported += 1
            else:
                failures.append(_safe_error(exc))
    if unsupported == len(PROBE_SYMBOLS):
        return CapabilityStatus(
            ProbeState.UNSUPPORTED, "all production probe symbols are unsupported"
        )
    return CapabilityStatus(
        ProbeState.UNAVAILABLE, failures[0] if failures else "endpoint unavailable"
    )


def _probe_one(
    operation: Callable[[], object],
    error_detail: Callable[[Exception], str] = _safe_error,
) -> CapabilityStatus:
    try:
        rows = _response_rows(operation())
        if not rows:
            raise ValueError("market-data response contained no usable rows")
        return CapabilityStatus(ProbeState.AVAILABLE)
    except Exception as exc:
        if _permission_failure(exc):
            return CapabilityStatus(ProbeState.NOT_ENTITLED, error_detail(exc))
        if _unsupported_symbol_failure(exc):
            return CapabilityStatus(ProbeState.UNSUPPORTED, error_detail(exc))
        return CapabilityStatus(
            ProbeState.UNAVAILABLE,
            "MALFORMED_RESPONSE" if isinstance(exc, ValueError) else error_detail(exc),
        )


def _probe_subscription(
    stream: object,
    symbol: str,
    error_detail: Callable[[Exception], str] = _safe_error,
) -> CapabilityStatus:
    try:
        stream.subscribe((symbol,))
    except Exception as exc:
        if _overnight_product_entitlement_failure(exc):
            return CapabilityStatus(
                ProbeState.NOT_ENTITLED,
                "OVERNIGHT_ENTITLEMENT_REQUIRED",
            )
        return CapabilityStatus(
            ProbeState.NOT_ENTITLED
            if _permission_failure(exc) else ProbeState.UNAVAILABLE,
            error_detail(exc),
        )
    attempted = CapabilityStatus(ProbeState.AVAILABLE)
    if not attempted.available:
        return attempted
    return _reported_capability(
        stream,
        "subscription_acknowledged",
        "stream subscription acknowledgement was not received",
        error_detail,
    )


def _overnight_product_entitlement_failure(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".upper().replace("-", "_")
    return (
        "MARKET_DATA_NOT_SUBSCRIBED" in text
        and "US_STOCK" in text
        and "OVERNIGHT" in text
    )


def _reported_capability(
    source: object,
    name: str,
    failure_detail: str,
    error_detail: Callable[[Exception], str] = _safe_error,
) -> CapabilityStatus:
    value = getattr(source, name, None)
    if value is None:
        # Compatibility transports expose synchronous success by returning
        # from connect/subscribe. Official transport exposes explicit state.
        return CapabilityStatus(ProbeState.AVAILABLE)
    try:
        accepted = value() if callable(value) else value
    except Exception as exc:
        return CapabilityStatus(ProbeState.UNAVAILABLE, error_detail(exc))
    return CapabilityStatus(
        ProbeState.AVAILABLE if accepted else ProbeState.UNAVAILABLE,
        "" if accepted else failure_detail,
    )


def _aggregate(values: tuple[CapabilityStatus, ...]) -> CapabilityStatus:
    if any(value.state is ProbeState.NOT_ENTITLED for value in values):
        return CapabilityStatus(ProbeState.NOT_ENTITLED, "permission denied")
    if values and all(value.state is ProbeState.UNSUPPORTED for value in values):
        return CapabilityStatus(ProbeState.UNSUPPORTED, "all probe symbols unsupported")
    if values and all(value.available for value in values):
        return CapabilityStatus(ProbeState.AVAILABLE)
    return CapabilityStatus(ProbeState.UNAVAILABLE, "one or more probe symbols failed")


def _symbol_state(values: tuple[CapabilityStatus, ...]) -> SymbolProbeState:
    if any(value.state is ProbeState.NOT_ENTITLED for value in values):
        return SymbolProbeState.NO_ENTITLEMENT
    if any(value.state is ProbeState.UNSUPPORTED for value in values):
        return SymbolProbeState.UNSUPPORTED
    if all(value.available for value in values):
        return SymbolProbeState.SUPPORTED
    return SymbolProbeState.UNKNOWN


def _call(
    operation: Callable[[], object],
    error_detail: Callable[[Exception], str] = _safe_error,
) -> CapabilityStatus:
    try:
        operation()
        return CapabilityStatus(ProbeState.AVAILABLE)
    except Exception as exc:
        return CapabilityStatus(
            ProbeState.NOT_ENTITLED if _permission_failure(exc) else ProbeState.UNAVAILABLE,
            error_detail(exc),
        )


def _debug_error(exc: Exception, credentials: tuple[str, str]) -> str:
    detail = f"{type(exc).__name__}: {_redact_error_text(str(exc), credentials)}"
    full_traceback = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    _LOG.exception(
        "Debug market-data capability probe failure:\n%s",
        _redact_error_text(full_traceback, credentials).rstrip(),
        exc_info=False,
    )
    return detail


def _redact_error_text(text: str, credentials: tuple[str, str]) -> str:
    redacted = _URL.sub(_redact_url, text)
    for credential in sorted(set(credentials), key=len, reverse=True):
        if len(credential) >= 4:
            redacted = redacted.replace(credential, "[REDACTED]")
    redacted = _AUTHORIZATION_VALUE.sub(
        lambda match: f"Authorization{match.group(1)}[REDACTED]", redacted
    )
    redacted = _SENSITIVE_VALUE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted
    )
    redacted = _BEARER_TOKEN.sub("Bearer [REDACTED]", redacted)
    return redacted


def _redact_url(match: re.Match[str]) -> str:
    value = match.group(0)
    trailing = ""
    while value and value[-1] in ".,;:":
        trailing = value[-1] + trailing
        value = value[:-1]
    parsed = urlsplit(value)
    safe_url = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
    return safe_url + trailing


__all__ = [
    "CapabilityStatus", "MarketDataCapabilityProbe", "MarketDataProbeResult",
    "PROBE_SYMBOLS", "SANDBOX_OPTIONAL_SYMBOLS", "SANDBOX_REQUIRED_SYMBOLS",
    "ProbeState", "SymbolCapabilityResult", "SymbolProbeState",
]

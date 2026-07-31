"""Independent startup capability probe for Webull market data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from app.configuration.models import MarketDataConfiguration
from app.webull.credential_identity import credential_fingerprint
from app.webull.sdk_market_data import (
    LazyOfficialDataClient,
    _permission_failure,
    _response_rows,
    _unsupported_symbol_failure,
)


PROBE_SYMBOLS = ("AAPL", "SPY", "TSLA")


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
    ) -> None:
        self._configuration = configuration
        self._client = client
        self._stream = stream

    def run(self) -> MarketDataProbeResult:
        cfg = self._configuration
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
            )

        try:
            client = self._client.get()
        except Exception as exc:
            denied = _permission_failure(exc)
            status = CapabilityStatus(
                ProbeState.NOT_ENTITLED if denied else ProbeState.UNAVAILABLE,
                _safe_error(exc),
            )
            not_tested = CapabilityStatus(ProbeState.NOT_TESTED)
            return MarketDataProbeResult(
                cfg.environment.value, fingerprint, status, status,
                not_tested, not_tested, not_tested, not_tested, not_tested,
                not_tested, not_tested, status if denied else not_tested,
                not_tested, (),
            )

        market_data = getattr(client, "market_data")
        instrument = getattr(client, "instrument")
        stream_status = _call(self._stream.connect)
        heartbeat = (
            _reported_capability(self._stream, "heartbeat_ok", "stream heartbeat unavailable")
            if stream_status.available else CapabilityStatus(ProbeState.NOT_TESTED)
        )
        reconnect = (
            _reported_capability(self._stream, "reconnect_ready", "stream reconnect unavailable")
            if stream_status.available else CapabilityStatus(ProbeState.NOT_TESTED)
        )
        symbol_results = []
        for symbol in PROBE_SYMBOLS:
            bar = _probe_one(lambda: market_data.get_history_bar(
                symbol, "US_STOCK", "D1", count="1", real_time_required=False
            ))
            quote = _probe_one(lambda: market_data.get_quotes(symbol, "US_STOCK"))
            snapshot = _probe_one(
                lambda: market_data.get_snapshot((symbol,), "US_STOCK")
            )
            reference_status = _probe_one(lambda: instrument.get_instrument(
                symbols=symbol, category="US_STOCK", page_size=1
            ))
            subscription_status = (
                _probe_subscription(self._stream, symbol)
                if stream_status.available else CapabilityStatus(ProbeState.NOT_TESTED)
            )
            capabilities = (bar, quote, snapshot, reference_status, subscription_status)
            symbol_results.append(SymbolCapabilityResult(
                symbol, bar, quote, snapshot, reference_status,
                subscription_status, _symbol_state(capabilities),
            ))

        bars = _aggregate(tuple(item.bars for item in symbol_results))
        quotes = _aggregate(tuple(item.quote for item in symbol_results))
        snapshots = _aggregate(tuple(item.snapshot for item in symbol_results))
        reference = _aggregate(tuple(item.reference for item in symbol_results))
        subscription = _aggregate(
            tuple(item.streaming_subscription for item in symbol_results)
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
        return MarketDataProbeResult(
            cfg.environment.value,
            fingerprint,
            CapabilityStatus(ProbeState.AVAILABLE),
            CapabilityStatus(ProbeState.AVAILABLE),
            bars, quotes, snapshots, stream_status, subscription,
            heartbeat, reconnect, entitlement, reference, tuple(symbol_results),
        )


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
            ProbeState.UNSUPPORTED, "AAPL, SPY, and TSLA are unsupported"
        )
    return CapabilityStatus(
        ProbeState.UNAVAILABLE, failures[0] if failures else "endpoint unavailable"
    )


def _probe_one(operation: Callable[[], object]) -> CapabilityStatus:
    try:
        _response_rows(operation())
        return CapabilityStatus(ProbeState.AVAILABLE)
    except Exception as exc:
        if _permission_failure(exc):
            return CapabilityStatus(ProbeState.NOT_ENTITLED, _safe_error(exc))
        if _unsupported_symbol_failure(exc):
            return CapabilityStatus(ProbeState.UNSUPPORTED, _safe_error(exc))
        return CapabilityStatus(ProbeState.UNAVAILABLE, _safe_error(exc))


def _probe_subscription(stream: object, symbol: str) -> CapabilityStatus:
    attempted = _call(lambda: stream.subscribe((symbol,)))
    if not attempted.available:
        return attempted
    return _reported_capability(
        stream,
        "subscription_acknowledged",
        "stream subscription acknowledgement was not received",
    )


def _reported_capability(
    source: object, name: str, failure_detail: str
) -> CapabilityStatus:
    value = getattr(source, name, None)
    if value is None:
        # Compatibility transports expose synchronous success by returning
        # from connect/subscribe. Official transport exposes explicit state.
        return CapabilityStatus(ProbeState.AVAILABLE)
    try:
        accepted = value() if callable(value) else value
    except Exception as exc:
        return CapabilityStatus(ProbeState.UNAVAILABLE, _safe_error(exc))
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


def _call(operation: Callable[[], object]) -> CapabilityStatus:
    try:
        operation()
        return CapabilityStatus(ProbeState.AVAILABLE)
    except Exception as exc:
        return CapabilityStatus(
            ProbeState.NOT_ENTITLED if _permission_failure(exc) else ProbeState.UNAVAILABLE,
            _safe_error(exc),
        )


def _safe_error(exc: Exception) -> str:
    # Exception text from third-party clients can contain signed request data.
    return type(exc).__name__


__all__ = [
    "CapabilityStatus", "MarketDataCapabilityProbe", "MarketDataProbeResult",
    "PROBE_SYMBOLS", "ProbeState", "SymbolCapabilityResult", "SymbolProbeState",
]

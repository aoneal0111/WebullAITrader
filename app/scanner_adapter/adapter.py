from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    MarketStatusPayload,
    QuotePayload,
    ResumePayload,
    TradePayload,
    TradingHaltPayload,
)
from app.momentum_scanner.models import ScannerObservation
from app.scanner_adapter.models import AdapterResult, SymbolScannerState
from app.scanner_adapter.reference_store import ScannerReferenceStore


class MarketEventScannerAdapter:
    """
    Converts broker-neutral MarketEvent objects into ScannerObservation objects.

    The adapter fails closed. It returns observation=None until all mandatory
    streaming and reference-data fields are available.
    """

    def __init__(
        self,
        reference_store: ScannerReferenceStore,
    ) -> None:
        self.reference_store = reference_store
        self._states: dict[str, SymbolScannerState] = {}

    def consume(self, event: MarketEvent) -> AdapterResult | None:
        if event.symbol is None:
            return None

        symbol = event.symbol.strip().upper()
        if not symbol:
            return None

        previous = self._states.get(symbol)
        if previous is None:
            reference = self.reference_store.get(symbol)
            previous = SymbolScannerState(
                symbol=symbol,
                cumulative_volume=(
                    reference.current_volume
                    if reference is not None and reference.current_volume is not None
                    else Decimal("0")
                ),
            )

        state = self._apply(previous, event)
        self._states[symbol] = state

        observation, missing = self._build_observation(state)

        return AdapterResult(
            state=state,
            observation=observation,
            missing_fields=missing,
        )

    def state_for(self, symbol: str) -> SymbolScannerState | None:
        return self._states.get(symbol.strip().upper())

    def reset_symbol(self, symbol: str) -> None:
        self._states.pop(symbol.strip().upper(), None)

    def reset_volume(self, symbol: str) -> None:
        normalized = symbol.strip().upper()
        current = self._states.get(normalized)

        if current is not None:
            self._states[normalized] = replace(
                current,
                cumulative_volume=Decimal("0"),
            )

    def observations(self) -> tuple[ScannerObservation, ...]:
        completed: list[ScannerObservation] = []

        for symbol in sorted(self._states):
            observation, _ = self._build_observation(
                self._states[symbol]
            )
            if observation is not None:
                completed.append(observation)

        return tuple(completed)

    def observation_for(self, symbol: str) -> ScannerObservation | None:
        state = self._states.get(symbol.strip().upper())
        if state is None:
            return None
        observation, _missing = self._build_observation(state)
        return observation

    def diagnostic_results(self, *, limit: int = 3) -> tuple[AdapterResult, ...]:
        """Return a bounded, immutable view of real per-symbol scanner inputs."""

        if limit < 1:
            raise ValueError("diagnostic limit must be positive")
        results: list[AdapterResult] = []
        for symbol in sorted(self._states)[:limit]:
            state = self._states[symbol]
            observation, missing = self._build_observation(state)
            results.append(AdapterResult(state, observation, missing))
        return tuple(results)

    def _apply(
        self,
        state: SymbolScannerState,
        event: MarketEvent,
    ) -> SymbolScannerState:
        if event.timestamp.tzinfo is None:
            raise ValueError("market event timestamp must be timezone-aware")

        if event.event_type is MarketEventType.QUOTE:
            if not isinstance(event.payload, QuotePayload):
                raise TypeError("QUOTE event requires QuotePayload")

            if (
                state.quote_timestamp is not None
                and event.timestamp < state.quote_timestamp
            ):
                return state
            return replace(
                state,
                timestamp=_latest_timestamp(state.timestamp, event.timestamp),
                quote_timestamp=event.timestamp,
                bid=event.payload.bid,
                ask=event.payload.ask,
            )

        if event.event_type is MarketEventType.TRADE:
            if not isinstance(event.payload, TradePayload):
                raise TypeError("TRADE event requires TradePayload")

            if event.payload.trade_id.startswith("snapshot"):
                if (
                    state.snapshot_timestamp is not None
                    and event.timestamp < state.snapshot_timestamp
                ):
                    return state
                freshest_price_timestamp = _latest_timestamp(
                    state.trade_timestamp, state.snapshot_timestamp
                )
                return replace(
                    state,
                    timestamp=_latest_timestamp(state.timestamp, event.timestamp),
                    snapshot_timestamp=event.timestamp,
                    last_price=(
                        event.payload.price
                        if freshest_price_timestamp is None
                        or event.timestamp >= freshest_price_timestamp
                        else state.last_price
                    ),
                    cumulative_volume=max(
                        state.cumulative_volume, event.payload.size
                    ),
                )

            if (
                state.trade_timestamp is not None
                and event.timestamp < state.trade_timestamp
            ):
                return state
            newest_snapshot = state.snapshot_timestamp
            is_newer_than_snapshot = (
                newest_snapshot is None or event.timestamp > newest_snapshot
            )
            return replace(
                state,
                timestamp=_latest_timestamp(state.timestamp, event.timestamp),
                trade_timestamp=event.timestamp,
                last_price=(
                    event.payload.price
                    if is_newer_than_snapshot
                    else state.last_price
                ),
                cumulative_volume=(
                    state.cumulative_volume + event.payload.size
                    if is_newer_than_snapshot
                    else state.cumulative_volume
                ),
            )

        if event.event_type is MarketEventType.TRADING_HALT:
            if not isinstance(event.payload, TradingHaltPayload):
                raise TypeError(
                    "TRADING_HALT event requires TradingHaltPayload"
                )

            return replace(
                state,
                timestamp=event.timestamp,
                halted=True,
            )

        if event.event_type is MarketEventType.RESUME:
            if not isinstance(event.payload, ResumePayload):
                raise TypeError("RESUME event requires ResumePayload")

            return replace(
                state,
                timestamp=event.timestamp,
                halted=False,
            )

        if event.event_type is MarketEventType.MARKET_STATUS:
            if not isinstance(event.payload, MarketStatusPayload):
                raise TypeError(
                    "MARKET_STATUS event requires MarketStatusPayload"
                )

            halted = event.payload.status.strip().upper() == "HALTED"

            return replace(
                state,
                timestamp=event.timestamp,
                halted=halted,
            )

        return state

    def _build_observation(
        self,
        state: SymbolScannerState,
    ) -> tuple[ScannerObservation | None, tuple[str, ...]]:
        reference = self.reference_store.get(state.symbol)
        missing: list[str] = []

        if state.timestamp is None:
            missing.append("timestamp")

        if state.last_price is None:
            missing.append("last_price")

        if state.bid is None:
            missing.append("bid")

        if state.ask is None:
            missing.append("ask")

        if state.cumulative_volume <= 0:
            missing.append("current_volume")

        if reference is None:
            missing.extend(
                (
                    "previous_close",
                    "average_30_day_volume",
                    "float_shares",
                    "catalyst",
                    "tradable",
                )
            )
        elif reference.float_shares is None:
            missing.append("float_shares")

        if missing:
            return None, tuple(missing)

        assert reference is not None
        assert state.timestamp is not None
        assert state.last_price is not None
        assert state.bid is not None
        assert state.ask is not None
        assert reference.float_shares is not None

        return (
            ScannerObservation(
                symbol=state.symbol,
                timestamp=state.timestamp,
                price=state.last_price,
                previous_close=reference.previous_close,
                current_volume=state.cumulative_volume,
                average_30_day_volume=(
                    reference.average_30_day_volume
                ),
                float_shares=reference.float_shares,
                catalyst=reference.catalyst,
                catalyst_headline=reference.catalyst_headline,
                catalyst_status=reference.catalyst_status,
                bid=state.bid,
                ask=state.ask,
                tradable=reference.tradable,
                halted=state.halted,
            ),
            (),
        )

    @property
    def state_count(self) -> int:
        return len(self._states)


def _latest_timestamp(
    left: datetime | None,
    right: datetime | None,
) -> datetime | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


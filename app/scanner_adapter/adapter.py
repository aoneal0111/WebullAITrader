from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
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
from app.market.calendar import EASTERN, trading_day_schedule
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
        *,
        price_observer: Callable[[str, datetime, Decimal], object] | None = None,
    ) -> None:
        self.reference_store = reference_store
        self._states: dict[str, SymbolScannerState] = {}
        self._active_trading_date: date | None = None
        self._price_observer = price_observer
        self._completeness_transitions: dict[str, dict[str, object]] = {}

    def consume(self, event: MarketEvent) -> AdapterResult | None:
        if event.symbol is None:
            return None

        symbol = event.symbol.strip().upper()
        if not symbol:
            return None

        trading_date = _effective_trading_date(event.timestamp)
        if trading_date is None:
            return None
        if self._active_trading_date is not None and trading_date < self._active_trading_date:
            return None
        if self._active_trading_date is None:
            self._active_trading_date = trading_date
        elif trading_date > self._active_trading_date:
            self._advance_trading_date(trading_date)

        previous = self._states.get(symbol)
        if previous is None:
            previous = self._new_state(symbol, trading_date)
        elif previous.trading_date != trading_date:
            previous = self._new_state(symbol, trading_date)

        state = self._apply(previous, event)
        self._states[symbol] = state

        if (
            self._price_observer is not None
            and event.event_type is MarketEventType.TRADE
            and isinstance(event.payload, TradePayload)
        ):
            if event.payload.trade_id.startswith("snapshot"):
                freshest_before = _latest_timestamp(
                    previous.trade_timestamp,
                    previous.snapshot_timestamp,
                )
                updates_price = (
                    event.payload.trade_id != "snapshot-retained-price"
                    and
                    (
                        previous.snapshot_timestamp is None
                        or event.timestamp >= previous.snapshot_timestamp
                    )
                    and (
                        freshest_before is None
                        or event.timestamp >= freshest_before
                    )
                )
            else:
                updates_price = (
                    (
                        previous.trade_timestamp is None
                        or event.timestamp >= previous.trade_timestamp
                    )
                    and (
                        previous.snapshot_timestamp is None
                        or event.timestamp > previous.snapshot_timestamp
                    )
                )

            if updates_price:
                self._price_observer(
                    symbol,
                    event.timestamp,
                    event.payload.price,
                )

        observation, missing = self._build_observation(state)
        self._update_completeness_transition(
            symbol,
            observation is not None,
            missing,
            event.timestamp,
        )

        return AdapterResult(
            state=state,
            observation=observation,
            missing_fields=missing,
        )

    def state_for(self, symbol: str) -> SymbolScannerState | None:
        return self._states.get(symbol.strip().upper())

    def memory_metrics(self) -> dict[str, int]:
        return {
            "symbol_state_count": len(self._states),
            "reference_count": len(self.reference_store),
        }

    def population_metrics(
        self,
        *,
        active_symbols: tuple[str, ...] | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Return bounded aggregate completeness diagnostics for current states."""
        observed_at = now or datetime.now(UTC)
        missing_counts = {
            name: 0 for name in (
                "timestamp", "last_price", "bid", "ask", "current_volume",
                "previous_close", "average_30_day_volume", "float_shares",
                "catalyst", "tradable", "other",
            )
        }
        for state in self._states.values():
            _observation, missing = self._build_observation(state)
            for field in missing:
                missing_counts[field if field in missing_counts else "other"] += 1
            self._update_completeness_transition(
                state.symbol,
                _observation is not None,
                missing,
                state.timestamp or observed_at,
            )
        selected = set(active_symbols) if active_symbols is not None else None
        transitions = {
            symbol: dict(values)
            for symbol, values in self._completeness_transitions.items()
            if selected is None or symbol in selected
        }
        for values in transitions.values():
            incomplete_since = values.get("incomplete_since")
            values["incomplete_age_ms"] = (
                None
                if incomplete_since is None
                else round(
                    max(0.0, (observed_at - incomplete_since).total_seconds())
                    * 1000.0,
                    3,
                )
            )
        return {
            "adapter_state_count": len(self._states),
            "missing_field_counts": missing_counts,
            "completeness_transitions": transitions,
        }

    def _update_completeness_transition(
        self,
        symbol: str,
        complete: bool,
        missing: tuple[str, ...],
        observed_at: datetime,
    ) -> None:
        current = self._completeness_transitions.get(symbol)
        prior_complete = None if current is None else bool(current["complete"])
        if current is None:
            current = {
                "first_seen_at": observed_at,
                "first_complete_at": observed_at if complete else None,
                "latest_state_at": observed_at,
                "complete": complete,
                "missing_fields": missing,
                "complete_transition_count": 1 if complete else 0,
                "incomplete_transition_count": 1 if not complete else 0,
                "incomplete_since": None if complete else observed_at,
            }
            self._completeness_transitions[symbol] = current
            return
        if prior_complete != complete:
            key = (
                "complete_transition_count"
                if complete
                else "incomplete_transition_count"
            )
            current[key] = int(current[key]) + 1
        if complete and current["first_complete_at"] is None:
            current["first_complete_at"] = observed_at
        current["latest_state_at"] = observed_at
        current["complete"] = complete
        current["missing_fields"] = missing
        current["incomplete_since"] = (
            None
            if complete
            else current["incomplete_since"]
            if prior_complete is False
            else observed_at
        )

    def reset_symbol(self, symbol: str) -> None:
        normalized = symbol.strip().upper()
        self._states.pop(normalized, None)
        self._completeness_transitions.pop(normalized, None)

    def reset_volume(self, symbol: str) -> None:
        normalized = symbol.strip().upper()
        current = self._states.get(normalized)

        if current is not None:
            self._states[normalized] = replace(
                current,
                cumulative_volume=Decimal("0"),
            )

    def _new_state(self, symbol: str, trading_date: date) -> SymbolScannerState:
        reference = self.reference_store.get(symbol)
        reference_date = (
            _effective_trading_date(reference.updated_at)
            if reference is not None and reference.updated_at is not None
            else None
        )
        seed = (
            reference.current_volume
            if (
                reference is not None
                and reference.current_volume is not None
                and reference_date == trading_date
            )
            else Decimal("0")
        )
        return SymbolScannerState(
            symbol=symbol,
            trading_date=trading_date,
            cumulative_volume=seed,
        )

    def _advance_trading_date(self, trading_date: date) -> None:
        self._active_trading_date = trading_date
        self._states = {
            symbol: replace(
                state,
                trading_date=trading_date,
                cumulative_volume=Decimal("0"),
            )
            for symbol, state in self._states.items()
        }

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
                quote_received_timestamp=event.received_timestamp,
                bid=event.payload.bid,
                ask=event.payload.ask,
                bid_size=event.payload.bid_size,
                ask_size=event.payload.ask_size,
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
                retained_price = (
                    event.payload.trade_id == "snapshot-retained-price"
                )
                return replace(
                    state,
                    timestamp=_latest_timestamp(state.timestamp, event.timestamp),
                    snapshot_timestamp=event.timestamp,
                    last_price_timestamp=(
                        event.timestamp
                        if not retained_price and (
                            freshest_price_timestamp is None
                            or event.timestamp >= freshest_price_timestamp
                        )
                        else state.last_price_timestamp
                    ),
                    last_price_received_timestamp=(
                        event.received_timestamp
                        if not retained_price and (
                            freshest_price_timestamp is None
                            or event.timestamp >= freshest_price_timestamp
                        )
                        else state.last_price_received_timestamp
                    ),
                    last_price=(
                        event.payload.price
                        if not retained_price and (
                            freshest_price_timestamp is None
                            or event.timestamp >= freshest_price_timestamp
                        )
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
                last_price_timestamp=(
                    event.timestamp
                    if is_newer_than_snapshot
                    else state.last_price_timestamp
                ),
                last_price_received_timestamp=(
                    event.received_timestamp
                    if is_newer_than_snapshot
                    else state.last_price_received_timestamp
                ),
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
                float_provenance=reference.float_provenance,
                catalyst=reference.catalyst,
                catalyst_headline=reference.catalyst_headline,
                catalyst_status=reference.catalyst_status,
                catalyst_source=reference.catalyst_source,
                catalyst_published_at=reference.catalyst_published_at,
                catalyst_source_url=reference.catalyst_source_url,
                corroborating_sources=reference.corroborating_sources,
                catalyst_evidence_count=reference.catalyst_evidence_count,
                catalyst_event_count=reference.catalyst_event_count,
                bid=state.bid,
                ask=state.ask,
                tradable=reference.tradable,
                halted=state.halted,
                last_price_timestamp=state.last_price_timestamp,
                quote_timestamp=state.quote_timestamp,
                trade_timestamp=state.trade_timestamp,
                last_price_received_timestamp=state.last_price_received_timestamp,
                quote_received_timestamp=state.quote_received_timestamp,
                bid_size=state.bid_size,
                ask_size=state.ask_size,
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


def _effective_trading_date(value: datetime | None) -> date | None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    schedule = trading_day_schedule(value.astimezone(EASTERN))
    return None if schedule is None else schedule.trading_date


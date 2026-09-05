"""Market-event adapter that can read snapshots but owns no execution port."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock

from .contracts import WorkingEntrySnapshot
from .material_change import detect_material_change, semantic_signature
from .persistence import JsonLinesResearchStore
from .worker import AdaptiveEntryResearchWorker, WorkerMetrics


_ACTIVE = {"NEW", "ACCEPTED", "PARTIALLY_FILLED"}


@dataclass(frozen=True, slots=True)
class RuntimeMetrics:
    enabled: bool
    observed_events: int
    eligible_orders: int
    suppressed: int
    failed: int
    retained_order_state: int
    retained_signatures: int
    worker: WorkerMetrics | None
    semantic_repeats_suppressed: int = 0
    concurrent_duplicate_suppressions: int = 0


class AdaptiveWorkingEntryObserver:
    def __init__(self, *, enabled: bool, environment: str, path: str | Path,
                 order_source: Callable[[str], Iterable[object]],
                 position_source: Callable[[str], object],
                 warrior_source: Callable[[str, datetime], Mapping[str, object] | None] | None = None,
                 capacity: int = 512, state_limit: int = 4096,
                 worker_factory: Callable[..., AdaptiveEntryResearchWorker] = AdaptiveEntryResearchWorker,
                 store_factory: Callable[[Path], object] = JsonLinesResearchStore,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        environment = environment.strip().upper()
        self.enabled = bool(enabled) and environment in {"TEST", "PAPER", "SANDBOX"}
        self.environment, self.path = environment, Path(path)
        self._orders, self._positions, self._warrior = order_source, position_source, warrior_source
        self._capacity, self._state_limit = capacity, state_limit
        self._worker_factory, self._store_factory = worker_factory, store_factory
        self._clock = clock
        self._worker: AdaptiveEntryResearchWorker | None = None
        self._state_lock = RLock()
        self._previous: OrderedDict[str, WorkingEntrySnapshot] = OrderedDict()
        self._signatures: OrderedDict[str, tuple[object, ...]] = OrderedDict()
        self._observed = self._eligible = self._suppressed = self._failed = 0
        self._semantic_repeats_suppressed = 0
        self._concurrent_duplicate_suppressions = 0

    def start(self, _environment: str | None = None) -> None:
        if not self.enabled or self._worker is not None:
            return
        try:
            if self.path.suffix.lower() != ".jsonl":
                raise ValueError("adaptive-entry research path must be JSONL")
            self._worker = self._worker_factory(
                self._store_factory(self.path),
                outcome_store=self._store_factory(self.path.with_name("outcomes.jsonl")),
                capacity=self._capacity, state_limit=self._state_limit,
            )
        except Exception:
            self._failed += 1

    def stop(self) -> None:
        worker = self._worker
        if worker is not None:
            try:
                worker.close()
            except Exception:
                self._failed += 1

    close = stop

    def __call__(self, event: object) -> None:
        if not self.enabled or self._worker is None:
            return
        try:
            symbol = str(getattr(event, "symbol", "") or "").strip().upper()
            market_event_at = getattr(event, "timestamp", None)
            if not symbol or not isinstance(market_event_at, datetime):
                return
            _require_aware(market_event_at, "market event timestamp")
            self._observed += 1
            payload = getattr(event, "payload", None)
            event_type = _value(getattr(event, "event_type", ""))
            outcome_price = None
            if event_type == "TRADE":
                outcome_price = _decimal(getattr(payload, "price", None))
            elif event_type == "QUOTE":
                outcome_bid = _decimal(getattr(payload, "bid", None))
                outcome_ask = _decimal(getattr(payload, "ask", None))
                if outcome_bid is not None and outcome_ask is not None:
                    outcome_price = (outcome_bid + outcome_ask) / 2
            outcome_observer = getattr(self._worker, "observe_market", None)
            if outcome_price is not None and callable(outcome_observer):
                outcome_observer(
                    symbol=symbol, observed_at=market_event_at, price=outcome_price,
                )
            for order in tuple(self._orders(symbol)):
                order_id = str(getattr(order, "order_id", ""))
                for _attempt in range(3):
                    with self._state_lock:
                        previous = self._previous.get(order_id)
                    snapshot = self._snapshot(order, event, market_event_at, previous=previous)
                    if snapshot is None:
                        break
                    self._eligible += 1
                    reasons = detect_material_change(previous, snapshot)
                    signature = semantic_signature(snapshot, reasons)
                    retry = False
                    admit = False
                    with self._state_lock:
                        # A producer may have raced while the snapshot was
                        # being built. Retry against the newest state rather
                        # than dropping a materially different transition.
                        current_previous = self._previous.get(snapshot.order_id)
                        if current_previous is not previous:
                            self._concurrent_duplicate_suppressions += 1
                            retry = True
                        elif not reasons:
                            if previous is not None:
                                self._suppressed += 1
                                self._semantic_repeats_suppressed += 1
                            self._remember(self._previous, snapshot.order_id, snapshot)
                        elif self._signatures.get(snapshot.order_id) == signature:
                            self._suppressed += 1
                            self._semantic_repeats_suppressed += 1
                        else:
                            # Reserve state atomically, but keep worker
                            # admission and all persistence/evaluation
                            # outside this short lock.
                            self._remember(self._previous, snapshot.order_id, snapshot)
                            self._remember(self._signatures, snapshot.order_id, signature)
                            admit = True
                    if retry:
                        continue
                    if admit:
                        self._worker.observe(snapshot, reasons)
                    break
        except Exception:
            self._failed += 1

    def metrics(self) -> RuntimeMetrics:
        with self._state_lock:
            previous_count, signature_count = len(self._previous), len(self._signatures)
            suppressed = self._suppressed
            semantic_suppressed = self._semantic_repeats_suppressed
            concurrent_suppressed = self._concurrent_duplicate_suppressions
        return RuntimeMetrics(self.enabled, self._observed, self._eligible, suppressed,
                              self._failed, previous_count, signature_count,
                              None if self._worker is None else self._worker.metrics(),
                              semantic_suppressed, concurrent_suppressed)

    def memory_metrics(self) -> dict[str, int]:
        """Read-only scalar cardinalities for optional diagnostics."""
        with self._state_lock:
            return {"previous_count": len(self._previous), "signature_count": len(self._signatures)}

    def _snapshot(
        self,
        order: object,
        event: object,
        market_event_at: datetime,
        *,
        previous: WorkingEntrySnapshot | None = None,
    ) -> WorkingEntrySnapshot | None:
        request = getattr(order, "request", None)
        status = _value(getattr(order, "status", ""))
        side, order_type = _value(getattr(request, "side", "")), _value(getattr(request, "order_type", ""))
        execution_reason = _value(getattr(request, "execution_reason", ""))
        lifecycle = str(getattr(request, "strategy_lifecycle_id", "") or "")
        if (
            status not in _ACTIVE
            or side != "BUY"
            or order_type != "LIMIT"
            or execution_reason != "ENTRY"
            or not lifecycle.startswith("WARRIOR_MOMENTUM_V1|")
        ):
            return None
        limit = _decimal(getattr(request, "limit_price", None))
        stop = _decimal(getattr(request, "structural_stop_price", None))
        valid_until = getattr(request, "entry_valid_until", None)
        if limit is None or stop is None or valid_until is None:
            return None
        payload = getattr(event, "payload", None)
        event_type = _value(getattr(event, "event_type", ""))
        bid, ask, last = (None if previous is None else previous.bid,
                          None if previous is None else previous.ask,
                          None if previous is None else previous.last)
        quote_time = None if previous is None else previous.quote_timestamp
        last_time = None if previous is None else previous.last_timestamp
        if event_type == "QUOTE":
            bid, ask, quote_time = _decimal(getattr(payload, "bid", None)), _decimal(getattr(payload, "ask", None)), market_event_at
        elif event_type == "TRADE":
            last = _decimal(getattr(payload, "price", None))
            last_time = market_event_at
        submitted_at = getattr(order, "created_at", None)
        order_state_at = getattr(order, "updated_at", submitted_at)
        if not isinstance(submitted_at, datetime) or not isinstance(order_state_at, datetime):
            return None
        _require_aware(submitted_at, "order submitted timestamp")
        _require_aware(order_state_at, "order state timestamp")
        observed_at = self._clock()
        _require_aware(observed_at, "adaptive observer clock")
        preliminary_cutoff = max(
            market_event_at,
            submitted_at,
            order_state_at,
            observed_at,
        )
        context = {} if self._warrior is None else dict(
            self._warrior(
                str(getattr(request, "symbol")), preliminary_cutoff,
            ) or {}
        )
        warrior_evidence_at = context.get("observed_at")
        if warrior_evidence_at is not None:
            if not isinstance(warrior_evidence_at, datetime):
                raise ValueError("Warrior context timestamp must be datetime")
            _require_aware(warrior_evidence_at, "Warrior context timestamp")
        position_value = self._positions(str(getattr(request, "symbol")))
        position_quantity, position_evidence_at = _position_evidence(
            position_value, observed_at,
        )
        observed_at = max(observed_at, self._clock())
        _require_aware(observed_at, "adaptive observer clock")
        cutoff = max(preliminary_cutoff, observed_at)
        observed_at = cutoff
        _reject_future(context, cutoff)
        if position_evidence_at > cutoff:
            raise ValueError("position evidence exceeds decision cutoff")
        original_quantity = int(Decimal(getattr(request, "quantity")))
        filled = int(Decimal(getattr(order, "filled_quantity", 0)))
        remaining = int(Decimal(getattr(order, "remaining_quantity", original_quantity - filled)))
        risk = abs(limit - stop)
        spread = None if bid is None or ask is None else ask - bid
        spread_percent = None if spread is None or ask == 0 else spread / ask * 100
        parts = lifecycle.split("|")
        unavailable = tuple(name for name, value in {
            "bid": bid, "ask": ask, "last": last,
            "scanner_rank": context.get("scanner_rank"), "scanner_score": context.get("scanner_score"),
            "momentum_velocity": context.get("momentum_velocity"), "volume_acceleration": context.get("volume_acceleration"),
        }.items() if value is None)
        freshness = None if quote_time is None else Decimal(str(max(0, (cutoff - quote_time).total_seconds())))
        return WorkingEntrySnapshot(
            schema_version="1",
            market_event_at=market_event_at,
            observed_at=observed_at,
            decision_cutoff=cutoff,
            environment=self.environment,
            symbol=str(getattr(request, "symbol")),
            strategy_id=parts[0],
            strategy_version=parts[0],
            strategy_lifecycle_id=lifecycle,
            setup_type=(
                parts[3]
                if len(parts) > 3
                else str(context.get("setup_type") or "UNKNOWN")
            ),
            setup_state=_optional(context.get("setup_state")),
            order_id=str(getattr(order, "order_id")),
            side=side,
            order_type=order_type,
            order_status=status,
            original_limit_price=limit,
            original_quantity=original_quantity,
            remaining_quantity=remaining,
            filled_quantity=filled,
            original_structural_stop=stop,
            original_risk_per_share=risk,
            original_total_risk=risk * original_quantity,
            order_submitted_at=submitted_at,
            order_state_at=order_state_at,
            entry_valid_until=valid_until,
            working_age_seconds=Decimal(
                str(max(0, (cutoff - submitted_at).total_seconds()))
            ),
            remaining_validity_seconds=Decimal(
                str((valid_until - cutoff).total_seconds())
            ),
            bid=bid,
            ask=ask,
            last=last,
            quote_timestamp=quote_time,
            last_timestamp=last_time,
            warrior_evidence_at=warrior_evidence_at,
            position_evidence_at=position_evidence_at,
            quote_freshness_seconds=freshness,
            spread=spread,
            spread_percent=spread_percent,
            scanner_rank=_integer(context.get("scanner_rank")),
            scanner_score=_decimal(context.get("scanner_score")),
            relative_volume=_decimal(context.get("relative_volume")),
            percentage_change=_decimal(context.get("percentage_change")),
            volume=_decimal(context.get("volume")),
            dollar_volume=_decimal(context.get("dollar_volume")),
            float_shares=_decimal(context.get("float_shares")),
            warrior_current_state=_optional(context.get("warrior_current_state")),
            current_reference_price=_decimal(context.get("current_reference_price")),
            current_structural_stop=_decimal(context.get("current_structural_stop")),
            current_setup_quality=_decimal(context.get("current_setup_quality")),
            current_technical_actionable=(
                context.get("current_technical_actionable")
                if isinstance(context.get("current_technical_actionable"), bool)
                else None
            ),
            existing_position_quantity=position_quantity,
            momentum_velocity=_decimal(context.get("momentum_velocity")),
            volume_acceleration=_decimal(context.get("volume_acceleration")),
            distance_from_hod_percent=_decimal(context.get("distance_from_hod_percent")),
            unavailable_evidence=unavailable,
        )

    def _remember(self, mapping: OrderedDict, key: str, value: object) -> None:
        mapping[key] = value
        mapping.move_to_end(key)
        while len(mapping) > self._state_limit:
            mapping.popitem(last=False)


def _value(value: object) -> str:
    return str(getattr(value, "value", value)).upper()


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(value)


def _integer(value: object) -> int | None:
    return None if value is None else int(value)


def _optional(value: object) -> str | None:
    return None if value is None else str(getattr(value, "value", value))


def _reject_future(context: Mapping[str, object], cutoff: datetime) -> None:
    observed_at = context.get("observed_at")
    if observed_at is not None and (not isinstance(observed_at, datetime) or observed_at > cutoff):
        raise ValueError("Warrior context exceeds decision cutoff")


def _position_evidence(
    value: object,
    adopted_at: datetime,
) -> tuple[int, datetime]:
    """Normalize a current-position snapshot and its adoption provenance."""

    if isinstance(value, tuple) and len(value) == 2:
        quantity_value, timestamp = value
        if not isinstance(timestamp, datetime):
            raise ValueError("position evidence timestamp must be datetime")
        _require_aware(timestamp, "position evidence timestamp")
    else:
        quantity_value, timestamp = value, adopted_at
    return int(Decimal(quantity_value)), timestamp


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


__all__ = ["AdaptiveWorkingEntryObserver", "RuntimeMetrics"]

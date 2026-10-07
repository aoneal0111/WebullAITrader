"""Canonical PAPER runtime adapter for the first-class Quick Scalper.

This module owns no broker client. Every mutation is delegated to the existing
autonomous bridge, TradingService, and PaperOrderGateway.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from threading import RLock
from typing import Callable
from uuid import uuid4

from app.paper_trading.order_book import PaperOrderBook
from app.paper_trading.order_models import OrderSide
from app.momentum_scanner.models import ScannerObservation

from .autonomous_paper import (
    AutonomousPaperExecutionBridge, PaperEntryAuthorizationResult,
    PaperExitSubmissionState,
)
from .configuration import RiskConfig
from .execution_quote import ExecutionQuoteSnapshot
from .forward_models import PaperAccountContext, PointInTimeObservation
from .models import MomentumCandidate
from .opportunity_engine import WarriorOpportunityEngine
from .profit_retention import (
    ProfitAction, ProfitRetentionPolicy, ProfitRetentionPosition, ProfitState,
)
from .quick_scalper import (
    CanonicalScalpSubmission, QuickScalpAssessment, QuickScalpSnapshot,
    QuickScalper, QuickScalperConfig, ScalpDecision, StrategyOwner,
    SymbolOwnershipRegistry,
)
from .risk import size_execution_position

ZERO = Decimal("0")
OWNER = StrategyOwner.QUICK_SCALPER.value
_STREAM_WINDOW = timedelta(seconds=30)
_MAX_STREAM_SAMPLES = 64
_MAX_STREAM_SYMBOLS = 512


@dataclass(frozen=True, slots=True)
class QuickScalperExecutionIntent:
    """Strategy-neutral contract consumed by the canonical PAPER bridge."""
    symbol: str
    timestamp: datetime
    session: str
    entry_trigger: Decimal
    reference_price: Decimal
    stop_price: Decimal
    risk_per_share: Decimal
    adaptive_target: Decimal
    lifecycle_id: str
    opportunity_id: str
    generation_id: str
    authorization_id: str
    execution_quote_id: str
    execution_quote_timestamp: datetime
    provider_last_timestamp: datetime
    provider_bid_timestamp: datetime
    provider_ask_timestamp: datetime
    strategy_owner: str = OWNER
    strategy_id: str = OWNER
    setup_type: str = "QUICK_SCALP"
    target_levels: tuple[Decimal, ...] = ()
    taxonomy_execution_identity: str | None = None
    taxonomy_opportunity_id: str | None = None

    def __post_init__(self) -> None:
        if self.entry_trigger <= self.stop_price or self.risk_per_share <= ZERO:
            raise ValueError("Quick Scalper intent requires positive structural risk")
        if self.adaptive_target <= self.entry_trigger:
            raise ValueError("Quick Scalper target must exceed entry")
        object.__setattr__(self, "target_levels", (self.adaptive_target,))
        object.__setattr__(self, "taxonomy_execution_identity", self.lifecycle_id)
        object.__setattr__(self, "taxonomy_opportunity_id", self.opportunity_id)


@dataclass(slots=True)
class _ScalpPositionState:
    symbol: str
    lifecycle_id: str
    generation_id: str
    entry_price: Decimal
    initial_stop: Decimal
    target_price: Decimal
    entered_at: datetime
    peak_bid: Decimal
    profit_state: ProfitState = ProfitState.NO_PROFIT


@dataclass(frozen=True, slots=True)
class _StreamQuoteSample:
    provider_timestamp: datetime
    last_timestamp: datetime
    last: Decimal
    bid: Decimal
    ask: Decimal
    ask_size: Decimal | None


class QuickScalperPaperRuntimeAdapter:
    """PAPER-only orchestration over existing canonical execution services."""

    def __init__(
        self, *, config: QuickScalperConfig,
        bridge: AutonomousPaperExecutionBridge, order_book: PaperOrderBook,
        account_context_source: Callable[[], PaperAccountContext | None],
        position_quantity_source: Callable[[str], Decimal],
        execution_quote_source: Callable[[str], ExecutionQuoteSnapshot | None],
        risk_config: RiskConfig = RiskConfig(),
        live_trading_enabled: bool = False, environment: str = "PAPER",
        engine: WarriorOpportunityEngine | None = None,
        ownership: SymbolOwnershipRegistry | None = None,
        event_sink: Callable[[str, dict[str, object]], None] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.bridge = bridge
        self.order_book = order_book
        self.account_context_source = account_context_source
        self.position_quantity_source = position_quantity_source
        self.execution_quote_source = execution_quote_source
        self.risk_config = risk_config
        self.live_trading_enabled = bool(live_trading_enabled)
        self.environment = str(environment).strip().upper()
        self.engine = engine or WarriorOpportunityEngine()
        self.ownership = ownership or SymbolOwnershipRegistry()
        self._event_sink = event_sink
        self._clock = clock
        self._snapshots: OrderedDict[
            tuple[str, str], QuickScalpSnapshot
        ] = OrderedDict()
        self._positions: dict[str, _ScalpPositionState] = {}
        self._closed_lifecycles: set[str] = set()
        self._current_generation: dict[str, str] = {}
        self._stream_samples: OrderedDict[
            str, deque[_StreamQuoteSample]
        ] = OrderedDict()
        self._stream_generation_started: dict[str, datetime] = {}
        self._lock = RLock()
        self.scalper = QuickScalper(
            engine=self.engine, ownership=self.ownership, config=config,
            authorize_and_submit=self._authorize_and_submit,
        )
        self.reconcile()

    @property
    def enabled(self) -> bool:
        return (
            self.config.enabled and self.environment == "PAPER"
            and not self.live_trading_enabled
        )

    def observe(
        self, point: PointInTimeObservation, candidate: MomentumCandidate,
        *, execution_permitted: bool = True,
    ) -> QuickScalpAssessment | None:
        """Consume existing streaming evaluation; never poll market data."""
        if not self.enabled:
            return None
        with self._lock:
            # Counter-only in the bounded observability sink. This preserves a
            # session-level assessed-symbol total even after raw-event capacity
            # is exhausted, without adding production files or provider calls.
            self._emit("SCALP_ASSESSED", candidate.symbol)
            self._manage_open_position(point, candidate)
            if not execution_permitted:
                return None
            snapshot = self._snapshot(point, candidate)
            if snapshot is None:
                return None
            prior = self._current_generation.get(snapshot.symbol)
            if prior is not None and prior != snapshot.generation_id:
                self._emit(
                    "SCALP_INVALIDATED", snapshot.symbol,
                    generation=prior, reason="SETUP_SUPERSEDED",
                )
            self._current_generation[snapshot.symbol] = snapshot.generation_id
            self._snapshots[(snapshot.symbol, snapshot.generation_id)] = snapshot
            self._emit("SCALP_OPPORTUNITY", snapshot.symbol,
                       generation=snapshot.generation_id)
            preview = self.scalper.policy.assess(snapshot)
            self._emit(
                "SCALP_ARMED", snapshot.symbol,
                generation=snapshot.generation_id,
            )
            event = (
                "SCALP_EXECUTABLE"
                if preview.decision is ScalpDecision.EXECUTABLE
                else "SCALP_EXECUTION_WAIT"
                if preview.decision is ScalpDecision.WAIT
                else "SCALP_REJECTED"
            )
            self._emit(
                event,
                snapshot.symbol, generation=snapshot.generation_id,
                reason=preview.reason,
            )
            # Authorization/submission follows the observable opportunity
            # state. The coordinator reuses the same pure policy assessment
            # and performs at most one authoritative quote request.
            result = self.scalper.observe(snapshot)
            return result

    def observe_stream_event(
        self, observation: ScannerObservation, *, decision_at: datetime,
        session: str, context: MomentumCandidate | None = None,
        execution_permitted: bool = True,
    ) -> QuickScalpAssessment | None:
        '''Assess fresh stream data without a Warrior setup or bar gate.

        The rolling state is bounded and observational. Authorization still
        crosses the canonical execution quote, risk, ownership, TradingService,
        PAPER gateway, and protection boundaries.
        '''
        if not self.enabled:
            return None
        symbol = observation.symbol.strip().upper()
        with self._lock:
            self._emit('SCALP_ASSESSED', symbol)
            snapshot = self._stream_snapshot(
                observation, decision_at=decision_at, session=session,
                context=context,
            )
            self._manage_open_position_values(
                symbol=symbol, executable_bid=observation.bid,
                quote_timestamp=observation.quote_timestamp,
                observed_at=decision_at,
                momentum_stalled=(
                    snapshot is None
                    or snapshot.velocity_cents_per_minute <= ZERO
                ),
            )
            if not execution_permitted or snapshot is None:
                return None
            prior = self._current_generation.get(snapshot.symbol)
            if prior is not None and prior != snapshot.generation_id:
                self._emit(
                    'SCALP_INVALIDATED', snapshot.symbol,
                    generation=prior, reason='SETUP_SUPERSEDED',
                )
            self._current_generation[snapshot.symbol] = snapshot.generation_id
            key = (snapshot.symbol, snapshot.generation_id)
            self._snapshots[key] = snapshot
            self._snapshots.move_to_end(key)
            while len(self._snapshots) > _MAX_STREAM_SYMBOLS * 2:
                self._snapshots.popitem(last=False)
            self._emit(
                'SCALP_OPPORTUNITY', snapshot.symbol,
                generation=snapshot.generation_id,
            )
            preview = self.scalper.policy.assess(snapshot)
            self._emit(
                'SCALP_ARMED', snapshot.symbol,
                generation=snapshot.generation_id,
            )
            event = (
                'SCALP_EXECUTABLE'
                if preview.decision is ScalpDecision.EXECUTABLE
                else 'SCALP_EXECUTION_WAIT'
                if preview.decision is ScalpDecision.WAIT
                else 'SCALP_REJECTED'
            )
            self._emit(
                event, snapshot.symbol, generation=snapshot.generation_id,
                reason=preview.reason,
            )
            return self.scalper.observe(snapshot)

    def current_opportunity(self, symbol: str):
        """Return the immutable generation-bound projection for GUI use."""
        return self.engine.get(symbol)

    def observe_paper_event(self, event: object) -> None:
        symbol = str(getattr(event, "symbol", "") or "").strip().upper()
        if symbol:
            with self._lock:
                order_view = getattr(event, "order", None)
                order_id = getattr(order_view, "order_id", None)
                try:
                    order = (
                        self.order_book.get(order_id)
                        if order_id is not None else None
                    )
                except Exception:
                    order = None
                if (
                    order is not None
                    and str(order.request.metadata.get(
                        "strategy_owner", "",
                    )).upper() == OWNER
                    and getattr(event, "fill", None) is not None
                ):
                    name = (
                        "SCALP_PARTIAL_FILL"
                        if order.request.side is OrderSide.BUY
                        else "SCALP_EXIT_FILLED"
                    )
                    self._emit(
                        name, symbol,
                        generation=order.request.strategy_lifecycle_id,
                        qty=int(getattr(event.fill, "quantity", 0)),
                    )
                self.reconcile(symbol)

    def reconcile(self, symbol: str | None = None) -> None:
        """Rebuild ownership/protection solely from canonical order history."""
        wanted = None if symbol is None else symbol.strip().upper()
        lifecycles: dict[str, list[object]] = {}
        for order in self.order_book.history():
            owner = str(order.request.metadata.get("strategy_owner", "")).upper()
            identity = order.request.strategy_lifecycle_id
            if owner != OWNER or not identity:
                continue
            if wanted is not None and order.symbol != wanted:
                continue
            lifecycles.setdefault(identity, []).append(order)
        for identity, orders in lifecycles.items():
            current_symbol = orders[0].symbol
            buys = [item for item in orders if item.request.side is OrderSide.BUY]
            if not buys:
                continue
            entry = min(buys, key=lambda item: (item.created_at, item.order_id))
            net = sum(
                (item.filled_quantity if item.request.side is OrderSide.BUY
                 else -item.filled_quantity for item in orders), ZERO,
            )
            working = any(not item.is_terminal for item in orders)
            open_buys = [
                item for item in orders
                if item.request.side is OrderSide.BUY and not item.is_terminal
            ]
            completed_exit = any(
                item.request.side is OrderSide.SELL
                and item.filled_quantity > ZERO for item in orders
            )
            if net <= ZERO and completed_exit and open_buys:
                self.bridge.cancel_working_entry_lifecycle(
                    current_symbol, identity, "SCALP_POSITION_FLAT",
                )
                working = any(
                    not item.is_terminal
                    for item in self.order_book.history()
                    if item.request.strategy_lifecycle_id == identity
                )
            if net > ZERO or working:
                self.ownership.acquire(
                    current_symbol, StrategyOwner.QUICK_SCALPER, identity,
                )
            if net > ZERO:
                entry_price = self._weighted_entry(buys)
                metadata = entry.request.metadata
                stop = self._decimal(metadata.get("structural_stop"))
                target = self._decimal(metadata.get("adaptive_target"))
                if entry_price is None or stop is None or target is None:
                    continue
                generation = str(
                    metadata.get("generation_id")
                    or metadata.get("opportunity_id") or identity
                )
                state = self._positions.get(current_symbol)
                entered_at = min(
                    (fill.timestamp for item in buys for fill in item.fills),
                    default=entry.updated_at,
                )
                if state is None or state.lifecycle_id != identity:
                    state = _ScalpPositionState(
                        current_symbol, identity, generation, entry_price, stop,
                        target, entered_at, entry_price,
                    )
                    self._positions[current_symbol] = state
                if any(not item.is_terminal for item in buys):
                    self.engine.mark_partially_filled(
                        current_symbol, generation, at=self._clock(),
                    )
                else:
                    self.engine.mark_entered(
                        current_symbol, generation, at=self._clock(),
                    )
                self._ensure_protection(state, int(net))
            elif not working:
                prior_state = self._positions.pop(current_symbol, None)
                self.ownership.release(
                    current_symbol, StrategyOwner.QUICK_SCALPER, identity,
                )
                if (
                    prior_state is not None
                    and identity not in self._closed_lifecycles
                ):
                    self.scalper.guard.record_close(
                        current_symbol, self._clock(),
                        self._realized_pnl(orders),
                    )
                    self._closed_lifecycles.add(identity)
                self._emit("SCALP_COMPLETED", current_symbol,
                           generation=identity)
                self.engine.complete(
                    current_symbol,
                    prior_state.generation_id if prior_state is not None else identity,
                    at=self._clock(),
                )

    def _ensure_protection(
        self, state: _ScalpPositionState, quantity: int,
    ) -> None:
        protection = self.bridge.ensure_exit(
            state.symbol, quantity, state.initial_stop, "STOP",
            state.lifecycle_id, strategy_owner=OWNER,
        )
        if not protection.protection_active:
            return
        target = self.bridge.ensure_exit(
            state.symbol, quantity, state.target_price, "SCALP_TARGET",
            state.lifecycle_id, target_stage_only=True, strategy_owner=OWNER,
        )
        if target.state in {
            PaperExitSubmissionState.SUBMITTED,
            PaperExitSubmissionState.WORKING,
            PaperExitSubmissionState.COMPLETED,
        }:
            self._emit("SCALP_PROTECTION_ACTIVE", state.symbol,
                       generation=state.generation_id, qty=quantity)
            self.engine.mark_protected(
                state.symbol, state.generation_id, at=self._clock(),
            )
            self.engine.mark_managing(
                state.symbol, state.generation_id, at=self._clock(),
            )

    def _authorize_and_submit(
        self, assessed: QuickScalpAssessment,
    ) -> CanonicalScalpSubmission:
        opportunity = assessed.opportunity
        source = self._snapshots.get(
            (opportunity.symbol, opportunity.generation_id)
        )
        if not self.enabled or source is None:
            return CanonicalScalpSubmission(
                False, False, False, False, "PAPER_ONLY_DISABLED",
            )
        # Exactly one authoritative confirmation call per authorization.
        quote = self.execution_quote_source(opportunity.symbol)
        if quote is None:
            return CanonicalScalpSubmission(
                False, False, False, False, "EXECUTION_QUOTE_UNAVAILABLE",
            )
        evaluated_at = self._clock()
        refreshed = replace(
            source, decision_at=evaluated_at, observed_at=evaluated_at,
            last=quote.last, bid=quote.bid, ask=quote.ask,
            last_timestamp=quote.last_timestamp,
            bid_timestamp=quote.bid_timestamp, ask_timestamp=quote.ask_timestamp,
        )
        confirmed = self.scalper.policy.assess(refreshed)
        if confirmed.decision is not ScalpDecision.EXECUTABLE:
            self._record_quote_decision(
                evaluated_at, quote, "REJECTED", confirmed.reason,
            )
            return CanonicalScalpSubmission(
                False, False, False, False, confirmed.reason,
            )
        account = self.account_context_source()
        if account is None:
            return CanonicalScalpSubmission(
                False, False, False, False, "ACCOUNT_UNAVAILABLE",
            )
        risk = quote.ask - refreshed.structural_stop
        sized = size_execution_position(
            symbol=refreshed.symbol, reference_price=quote.ask,
            risk_per_share=risk, account_equity=account.equity,
            buying_power=account.buying_power,
            allowed_symbols=account.allowed_symbols,
            existing_exposure=account.existing_exposure,
            exposure_limit=account.exposure_limit,
            risk_engine_approved=account.risk_engine_approved,
            broker_restriction=account.broker_restriction,
            config=self.risk_config,
        )
        if not sized.approved:
            self._record_quote_decision(
                evaluated_at, quote, "REJECTED", "RISK_REJECTED",
            )
            return CanonicalScalpSubmission(
                False, False, False, False, "RISK_REJECTED",
            )
        shares = sized.shares
        if source.executable_ask_size is not None:
            executable_cap = int((
                source.executable_ask_size
                * self.config.maximum_top_of_book_participation_fraction
            ).to_integral_value(rounding=ROUND_FLOOR))
            shares = min(shares, executable_cap)
            if shares <= 0:
                self._record_quote_decision(
                    evaluated_at, quote, 'REJECTED',
                    'INSUFFICIENT_EXECUTABLE_SIZE',
                )
                return CanonicalScalpSubmission(
                    False, False, False, False,
                    'INSUFFICIENT_EXECUTABLE_SIZE',
                )
        lifecycle = confirmed.opportunity.lifecycle_id
        opportunity_id = (
            f"QUICK_SCALPER|{refreshed.symbol}|{refreshed.generation_id}"
        )
        intent = QuickScalperExecutionIntent(
            symbol=refreshed.symbol, timestamp=evaluated_at,
            session=refreshed.session, entry_trigger=quote.ask,
            reference_price=quote.ask, stop_price=refreshed.structural_stop,
            risk_per_share=risk, adaptive_target=confirmed.target_price,
            lifecycle_id=lifecycle, opportunity_id=opportunity_id,
            generation_id=refreshed.generation_id,
            authorization_id=uuid4().hex, execution_quote_id=uuid4().hex,
            execution_quote_timestamp=quote.confirmed_at or evaluated_at,
            provider_last_timestamp=quote.last_timestamp,
            provider_bid_timestamp=quote.bid_timestamp,
            provider_ask_timestamp=quote.ask_timestamp,
        )
        self._emit(
            "SCALP_ORDER_INTENT", refreshed.symbol,
            generation=refreshed.generation_id, qty=shares,
        )
        decision = self.bridge.submit_entry_decision(
            intent, shares, risk * shares,
            opportunity_id=opportunity_id, opportunity_anchor=quote.ask,
            provenance="QUICK_SCALPER_CANONICAL_ENTRY",
        )
        accepted = (
            decision.result is PaperEntryAuthorizationResult.AUTHORIZED
        )
        self._record_quote_decision(
            evaluated_at, quote, "ACCEPTED" if accepted else "REJECTED",
            None if accepted else decision.reason.value,
        )
        self._emit(
            "SCALP_AUTHORIZED" if accepted else "SCALP_REJECTED",
            refreshed.symbol, generation=refreshed.generation_id,
            qty=shares, reason=decision.reason.value,
        )
        if accepted:
            self._emit(
                "SCALP_ORDER_SUBMITTED", refreshed.symbol,
                generation=refreshed.generation_id, qty=shares,
                reason=decision.reason.value,
            )
        return CanonicalScalpSubmission(
            accepted, decision.order_constructed, accepted, False,
            decision.reason.value, lifecycle, decision.placement_decision,
        )

    def _stream_snapshot(
        self, observation: ScannerObservation, *, decision_at: datetime,
        session: str, context: MomentumCandidate | None,
    ) -> QuickScalpSnapshot | None:
        symbol = observation.symbol.strip().upper()
        quote_timestamp = observation.quote_timestamp
        last_timestamp = observation.last_price_timestamp
        if (
            not observation.tradable or observation.halted
            or observation.bid is None or observation.ask is None
            or quote_timestamp is None or last_timestamp is None
        ):
            return None
        sample = _StreamQuoteSample(
            quote_timestamp, last_timestamp, observation.price,
            observation.bid, observation.ask, observation.ask_size,
        )
        samples = self._stream_samples.get(symbol)
        if samples is None:
            if len(self._stream_samples) >= _MAX_STREAM_SYMBOLS:
                evicted, _ = self._stream_samples.popitem(last=False)
                self._stream_generation_started.pop(evicted, None)
            samples = deque(maxlen=_MAX_STREAM_SAMPLES)
            self._stream_samples[symbol] = samples
        else:
            self._stream_samples.move_to_end(symbol)
        if samples and quote_timestamp < samples[-1].provider_timestamp:
            return None
        if (
            samples
            and quote_timestamp - samples[-1].provider_timestamp > _STREAM_WINDOW
        ):
            samples.clear()
            self._stream_generation_started.pop(symbol, None)
        samples.append(sample)
        cutoff = quote_timestamp - _STREAM_WINDOW
        while samples and samples[0].provider_timestamp < cutoff:
            samples.popleft()
        if len(samples) < 2:
            self._stream_generation_started[symbol] = quote_timestamp
            return None
        first = samples[0]
        elapsed = Decimal(str(
            (quote_timestamp - first.provider_timestamp).total_seconds()
        ))
        if elapsed <= ZERO:
            return None
        move = observation.bid - first.bid
        if move <= ZERO:
            return None
        velocity = move * Decimal('60') / elapsed
        percent_velocity = (
            velocity / first.ask * Decimal('100')
            if first.ask > ZERO else ZERO
        )
        structural_stop = min(item.bid for item in samples)
        if structural_stop <= ZERO or structural_stop >= observation.ask:
            return None
        short_range = max(item.bid for item in samples) - structural_stop
        generation_started = self._stream_generation_started.setdefault(
            symbol, first.provider_timestamp,
        )
        generation = sha256(
            f'{symbol}|{generation_started.isoformat()}|QUICK_SCALP_STREAM'.encode(
                'utf-8'
            )
        ).hexdigest()[:24]
        relative_volume = ZERO
        relative_volume_status = 'UNAVAILABLE'
        if observation.average_30_day_volume > ZERO:
            relative_volume = (
                observation.current_volume / observation.average_30_day_volume
            )
            relative_volume_status = 'AVAILABLE'
        setup_quality = ZERO
        momentum_priority = ZERO
        catalyst_context = observation.catalyst_status.value
        if context is not None:
            setup_quality = context.score.total
            momentum_priority = context.momentum_priority
            catalyst_context = context.catalyst_status.value
            status = getattr(context, 'relative_volume_status', None)
            if status is not None:
                relative_volume_status = status
                relative_volume = context.relative_volume
        return QuickScalpSnapshot(
            symbol=symbol, generation_id=generation,
            setup_type='QUICK_SCALP_STREAM',
            observed_at=observation.timestamp, decision_at=decision_at,
            last=observation.price, bid=observation.bid, ask=observation.ask,
            last_timestamp=last_timestamp,
            bid_timestamp=quote_timestamp, ask_timestamp=quote_timestamp,
            structural_stop=structural_stop,
            short_horizon_range=short_range,
            velocity_cents_per_minute=velocity,
            velocity_percent_per_minute=percent_velocity,
            relative_volume=relative_volume,
            dollar_liquidity=observation.price * observation.current_volume,
            setup_quality=setup_quality,
            momentum_priority=momentum_priority,
            catalyst_context=catalyst_context,
            session=session,
            relative_volume_status=relative_volume_status,
            executable_ask_size=observation.ask_size,
        )

    def _snapshot(
        self, point: PointInTimeObservation, candidate: MomentumCandidate,
    ) -> QuickScalpSnapshot | None:
        obs = point.observation
        velocity = candidate.price_velocity_cents_1m
        percent_velocity = candidate.price_velocity_percent_1m
        if (
            not candidate.tradable
            or candidate.halted or obs.bid is None or obs.ask is None
            or velocity is None or velocity <= ZERO
            or percent_velocity is None or percent_velocity <= ZERO
            or not point.bars
        ):
            return None
        evaluated_at = point.evaluation_timestamp or self._clock()
        last_ts = point.last_price_observed_at or obs.last_price_timestamp
        quote_ts = point.quote_observed_at or obs.quote_timestamp
        if last_ts is None or quote_ts is None:
            return None
        recent = point.bars[-3:]
        structural_stop = min(bar.low for bar in recent)
        if structural_stop <= ZERO or structural_stop >= obs.ask:
            return None
        short_range = max(bar.high for bar in recent) - structural_stop
        # Quick Scalper generation identity is independent of Warrior setup
        # maturity and replacement. The completed-bar structure remains stable
        # across streaming reevaluation of the same scalp opportunity.
        material = (
            f"{candidate.symbol}|{recent[-1].timestamp.isoformat()}|"
            f"{structural_stop}"
        )
        generation = sha256(material.encode("utf-8")).hexdigest()[:24]
        return QuickScalpSnapshot(
            symbol=candidate.symbol, generation_id=generation,
            setup_type="QUICK_SCALP_MOMENTUM",
            observed_at=obs.timestamp, decision_at=evaluated_at,
            last=obs.price, bid=obs.bid, ask=obs.ask,
            last_timestamp=last_ts, bid_timestamp=quote_ts,
            ask_timestamp=quote_ts, structural_stop=structural_stop,
            short_horizon_range=short_range,
            velocity_cents_per_minute=velocity,
            velocity_percent_per_minute=percent_velocity,
            relative_volume=candidate.relative_volume,
            dollar_liquidity=candidate.dollar_volume,
            setup_quality=candidate.score.total,
            momentum_priority=candidate.momentum_priority,
            catalyst_context=candidate.catalyst_status.value,
            session=point.session,
            relative_volume_status=(
                getattr(candidate, 'relative_volume_status', None)
                or ('AVAILABLE' if candidate.relative_volume > ZERO else 'UNAVAILABLE')
            ),
            executable_ask_size=getattr(point, 'best_ask_size', None),
        )

    def _manage_open_position(
        self, point: PointInTimeObservation, candidate: MomentumCandidate,
    ) -> None:
        obs = point.observation
        self._manage_open_position_values(
            symbol=candidate.symbol,
            executable_bid=obs.bid,
            quote_timestamp=(point.quote_observed_at or obs.quote_timestamp),
            observed_at=(point.evaluation_timestamp or self._clock()),
            momentum_stalled=(
                candidate.price_velocity_cents_1m is not None
                and candidate.price_velocity_cents_1m <= ZERO
            ),
        )

    def _manage_open_position_values(
        self, *, symbol: str, executable_bid: Decimal | None,
        quote_timestamp: datetime | None, observed_at: datetime,
        momentum_stalled: bool,
    ) -> None:
        """Manage an owned scalp from canonical executable stream values."""
        state = self._positions.get(symbol)
        if state is None or executable_bid is None or quote_timestamp is None:
            return
        age = Decimal(str((observed_at - quote_timestamp).total_seconds()))
        if age < ZERO or age > self.config.provider_freshness_seconds:
            return
        decision = ProfitRetentionPolicy().evaluate(
            ProfitRetentionPosition(
                StrategyOwner.QUICK_SCALPER, state.symbol, state.lifecycle_id,
                state.entered_at, state.entry_price, state.initial_stop,
                self._current_stop(state), state.peak_bid, state.profit_state,
            ),
            executable_bid=executable_bid, observed_at=observed_at,
            momentum_stalled=momentum_stalled,
        )
        state.peak_bid = decision.position.peak_executable_bid
        state.profit_state = decision.position.state
        quantity = max(0, int(self.position_quantity_source(state.symbol)))
        if quantity <= 0:
            self.reconcile(state.symbol)
            return
        if (
            decision.action is ProfitAction.TIGHTEN_STOP
            and decision.desired_stop is not None
        ):
            self.bridge.ensure_exit(
                state.symbol, quantity, decision.desired_stop, "STOP",
                state.lifecycle_id, strategy_owner=OWNER,
            )
            self._emit(
                "SCALP_PROFIT_DEFENSE", state.symbol,
                generation=state.generation_id, reason=decision.reason,
            )
        elif decision.action is ProfitAction.FULL_EXIT:
            result = self.bridge.ensure_exit(
                state.symbol, quantity, executable_bid, decision.reason,
                state.lifecycle_id, strategy_owner=OWNER,
            )
            if result.state in {
                PaperExitSubmissionState.SUBMITTED,
                PaperExitSubmissionState.WORKING,
            }:
                self._emit(
                    "SCALP_EXIT_REQUESTED", state.symbol,
                    generation=state.generation_id,
                    reason=decision.reason, qty=quantity,
                )
                self.engine.mark_exit_requested(
                    state.symbol, state.generation_id, at=observed_at,
                )

    def _current_stop(self, state: _ScalpPositionState) -> Decimal:
        values = [
            order.request.stop_price
            for order in self.order_book.open_orders_for_symbol(state.symbol)
            if order.request.strategy_lifecycle_id == state.lifecycle_id
            and order.request.stop_price is not None
        ]
        return max(values, default=state.initial_stop)

    @staticmethod
    def _weighted_entry(orders: list[object]) -> Decimal | None:
        quantity = sum((item.filled_quantity for item in orders), ZERO)
        if quantity <= ZERO:
            return None
        notional = sum(
            (item.filled_quantity * item.average_fill_price
             for item in orders if item.average_fill_price is not None), ZERO,
        )
        return notional / quantity

    @staticmethod
    def _realized_pnl(orders: list[object]) -> Decimal:
        buys = sum(
            (item.filled_quantity * item.average_fill_price
             for item in orders
             if item.request.side is OrderSide.BUY
             and item.average_fill_price is not None), ZERO,
        )
        sells = sum(
            (item.filled_quantity * item.average_fill_price
             for item in orders
             if item.request.side is OrderSide.SELL
             and item.average_fill_price is not None), ZERO,
        )
        return sells - buys

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        try:
            parsed = Decimal(str(value))
        except Exception:
            return None
        return parsed if parsed.is_finite() and parsed > ZERO else None

    def _record_quote_decision(
        self, evaluated_at: datetime, quote: ExecutionQuoteSnapshot,
        outcome: str, reason: str | None,
    ) -> None:
        callback = getattr(
            self.execution_quote_source, "record_decision", None,
        )
        if callable(callback):
            try:
                callback(
                    evaluated_at=evaluated_at, snapshot=quote,
                    outcome=outcome, rejection_reason=reason,
                )
            except Exception:
                pass

    def _emit(self, event: str, symbol: str, **details: object) -> None:
        if self._event_sink is None:
            return
        allowed = {
            key: value for key, value in details.items()
            if key in {"generation", "reason", "qty", "state"}
        }
        try:
            self._event_sink(event, {
                "symbol": symbol, "strategy_owner": OWNER, **allowed,
            })
        except Exception:
            pass


__all__ = [
    "QuickScalperExecutionIntent", "QuickScalperPaperRuntimeAdapter",
]

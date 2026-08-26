"""Point-in-time Warrior observation, paper lifecycle, and counterfactual sidecar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from typing import Callable

from .configuration import WarriorMomentumConfig
from .features import build_features, completed_bars_as_of
from .forward_models import (
    CaptureRecord, CaptureRecordType, FloatProvenance,
    ForwardCaptureConfiguration, ForwardTransition, PaperAccountContext,
    PointInTimeObservation,
)
from .autonomous_paper import lifecycle_identity
from .forward_queue import ForwardCaptureWriter
from .forward_store import ForwardCaptureStore
from .models import (
    CandidateStatus, MinuteBar, MomentumCandidate, MomentumEntrySignal,
    ReasonCode, SetupState,
)
from .risk import size_position
from .runtime import WarriorMomentumRuntime

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(slots=True)
class _PaperState:
    signal: MomentumEntrySignal
    entry_price: Decimal
    initial_quantity: int
    remaining: int
    stop: Decimal
    first_quantity: int
    second_quantity: int
    first_taken: bool = False
    second_taken: bool = False
    realized_pnl: Decimal = ZERO
    last_bar_timestamp: datetime | None = None
    prior_low: Decimal | None = None
    minimum_low: Decimal | None = None
    maximum_high: Decimal | None = None


@dataclass(slots=True)
class _CounterState:
    symbol: str
    started_at: datetime
    trigger: Decimal
    stop: Decimal
    bars_observed: int = 0
    last_bar_timestamp: datetime | None = None


class WarriorForwardCaptureService:
    """Consumes sanitized observations and never owns a broker/order port."""

    def __init__(
        self, store: ForwardCaptureStore, writer: ForwardCaptureWriter,
        config: WarriorMomentumConfig = WarriorMomentumConfig(),
        capture_config: ForwardCaptureConfiguration = ForwardCaptureConfiguration(),
        paper_entry_submitter: Callable[[MomentumEntrySignal, int, Decimal], bool] | None = None,
        paper_exit_submitter: Callable[[str, int, Decimal, str, str | None], bool] | None = None,
        paper_position_quantity_source: Callable[[str], Decimal] | None = None,
    ) -> None:
        self.store = store
        self.writer = writer
        self.config = config
        self.capture_config = capture_config
        self._paper_entry_submitter = paper_entry_submitter
        self._paper_exit_submitter = paper_exit_submitter
        self._paper_position_quantity_source = paper_position_quantity_source
        self.runtime = WarriorMomentumRuntime(config)
        self._last_transition: dict[str, ForwardTransition] = {}
        self._seen_bars: set[tuple[str, datetime]] = set()
        self._paper: dict[str, _PaperState] = {}
        self._counterfactual: dict[str, _CounterState] = {}
        self._recover()

    def observe(
        self, value: PointInTimeObservation,
        *, account: PaperAccountContext | None = None,
    ) -> tuple[MomentumCandidate, MomentumEntrySignal | None]:
        observation = value.observation
        symbol = observation.symbol.strip().upper()
        completed = completed_bars_as_of(value.bars, observation.timestamp)
        for bar in completed:
            self.observe_market_bar(symbol, bar, observation.timestamp)
        candidate = self.runtime.discover(
            observation, completed, session=value.session,
        )
        assessed, signal = self.runtime.assess_entry(candidate)
        features = build_features(completed)
        records: list[CaptureRecord] = []
        records.extend(self._evidence_records(value))
        records.append(_discovery_record(value, assessed))
        records.append(_decision_record(value, assessed, completed, features))
        already_open = signal is not None and signal.symbol in self._paper
        records.extend(self._transition_records(
            assessed, None if already_open else signal, account=account,
        ))
        records.append(_quality_record(value, completed, self.capture_config))

        if signal is not None:
            if signal.symbol in self._paper:
                records.append(_transition_record(
                    assessed, ForwardTransition.ENTRY_BLOCKED,
                    (ReasonCode.EXECUTION_NOT_ALLOWED.value,),
                    ({"gate": "existing_paper_position", "passed": False,
                      "observed": True, "limit": False},),
                ))
                signal = None
            elif not value.halt_state_known:
                blocked = _transition_record(
                    assessed, ForwardTransition.ENTRY_BLOCKED,
                    (ReasonCode.HALT_UNKNOWN.value,),
                    (*_gate_diagnostics(assessed, self.config, account=account),
                     {"gate": "halt_certainty", "passed": False,
                      "observed": "UNKNOWN", "limit": "KNOWN"}),
                )
                records.append(blocked)
                signal = None
            elif account is None:
                blocked = _transition_record(
                    assessed, ForwardTransition.ENTRY_BLOCKED,
                    (ReasonCode.EXECUTION_NOT_ALLOWED.value,),
                    _gate_diagnostics(assessed, self.config, account=None),
                )
                records.append(blocked)
                signal = None
            else:
                position = size_position(
                    signal, account_equity=account.equity,
                    buying_power=account.buying_power,
                    allowed_symbols=account.allowed_symbols,
                    existing_exposure=account.existing_exposure,
                    exposure_limit=account.exposure_limit,
                    risk_engine_approved=account.risk_engine_approved,
                    broker_restriction=account.broker_restriction,
                    config=self.config.risk,
                )
                if position.approved:
                    entry_records = self._open_paper(
                        signal, position.shares, position.risk_dollars,
                        value.float_provenance,
                    )
                    records.extend(entry_records)
                    # A configured execution bridge is authoritative for the
                    # entry boundary.  If it rejects the command, do not
                    # return an apparently executable signal to callers.
                    if self._paper_entry_submitter is not None and not entry_records:
                        signal = None
                else:
                    records.append(_transition_record(
                        assessed, ForwardTransition.ENTRY_BLOCKED,
                        tuple(code.value for code in position.reason_codes),
                        (*_gate_diagnostics(assessed, self.config, account=account),
                         *_account_gate_diagnostics(signal, account)),
                    ))
                    signal = None

        setup = assessed.setup
        if (
            setup is not None and setup.state is SetupState.TRIGGERED
            and assessed.score.total >= self.config.entry.minimum_momentum_score
            and signal is None and assessed.symbol not in self._paper
            and setup.trigger is not None and setup.stop_price is not None
        ):
            records.extend(self._start_counterfactual(assessed))
        self.writer.submit_many(tuple(records))
        return assessed, signal

    def observe_market_bar(self, symbol: str, bar: MinuteBar, observed_at) -> None:
        """Advance retained paper/counterfactual state independent of ranking."""
        normalized = symbol.strip().upper()
        records: list[CaptureRecord] = []
        bar_key = (normalized, bar.timestamp)
        if bar_key not in self._seen_bars:
            records.append(_bar_record(bar, observed_at))
            self._seen_bars.add(bar_key)
        state = self._paper.get(normalized)
        if state is not None and bar.timestamp >= state.signal.timestamp:
            if state.last_bar_timestamp is None or bar.timestamp > state.last_bar_timestamp:
                records.extend(self._advance_paper(state, bar, observed_at))
        counter = self._counterfactual.get(normalized)
        if counter is not None:
            if counter.last_bar_timestamp is None or bar.timestamp > counter.last_bar_timestamp:
                counter.bars_observed += 1
                counter.last_bar_timestamp = bar.timestamp
                risk = counter.trigger - counter.stop
                records.append(CaptureRecord.create(
                    CaptureRecordType.COUNTERFACTUAL, normalized, observed_at,
                    {"action": "PATH", "source_bar_timestamp": bar.timestamp,
                     "open": bar.open, "high": bar.high, "low": bar.low,
                     "close": bar.close, "volume": bar.volume,
                     "high_r": None if risk <= 0 else (bar.high - counter.trigger) / risk,
                     "low_r": None if risk <= 0 else (bar.low - counter.trigger) / risk,
                     "bars_observed": counter.bars_observed},
                    identity_parts=(bar.timestamp.isoformat(),),
                ))
                if counter.bars_observed >= self.capture_config.counterfactual_bars:
                    records.append(CaptureRecord.create(
                        CaptureRecordType.COUNTERFACTUAL, normalized, observed_at,
                        {"action": "END", "bars_observed": counter.bars_observed},
                        identity_parts=("END", str(counter.started_at)),
                    ))
                    self._counterfactual.pop(normalized, None)
        if records:
            self.writer.submit_many(tuple(records))

    def _evidence_records(self, value: PointInTimeObservation) -> tuple[CaptureRecord, ...]:
        observation = value.observation
        midpoint = spread_dollars = spread_percent = None
        if observation.bid is not None and observation.ask is not None:
            midpoint = (observation.bid + observation.ask) / Decimal("2")
            spread_dollars = observation.ask - observation.bid
            spread_percent = spread_dollars / midpoint * HUNDRED
        catalyst = CaptureRecord.create(
            CaptureRecordType.CATALYST_EVIDENCE, observation.symbol, observation.timestamp,
            {"evidence_state": observation.catalyst_status.value,
             "event_type": observation.catalyst.value,
             "event_timestamp": value.catalyst_event_timestamp,
             "event_date": value.catalyst_event_date,
             "observation_timestamp": observation.timestamp,
             "source": value.catalyst_source,
             "source_classification": value.catalyst_source_classification},
        )
        spread = CaptureRecord.create(
            CaptureRecordType.SPREAD_EVIDENCE, observation.symbol, observation.timestamp,
            {"bid": observation.bid, "ask": observation.ask, "midpoint": midpoint,
             "spread_dollars": spread_dollars, "spread_percent": spread_percent,
             "observation_timestamp": value.quote_observed_at or observation.timestamp,
             "freshness_seconds": value.quote_freshness_seconds,
             "authoritative": observation.bid is not None and observation.ask is not None},
        )
        return catalyst, spread

    def _transition_records(
        self, candidate: MomentumCandidate, signal: MomentumEntrySignal | None,
        *, account: PaperAccountContext | None,
    ) -> tuple[CaptureRecord, ...]:
        symbol = candidate.symbol
        transitions: list[ForwardTransition] = []
        if symbol not in self._last_transition:
            transitions.append(ForwardTransition.DISCOVERED)
        mapping = {
            CandidateStatus.DISCOVERED: ForwardTransition.DISCOVERED,
            CandidateStatus.WATCH: ForwardTransition.WATCH,
            CandidateStatus.NEAR_QUALIFIED: ForwardTransition.NEAR,
            CandidateStatus.QUALIFIED: ForwardTransition.QUALIFIED,
            CandidateStatus.SETUP_FORMING: ForwardTransition.SETUP_FORMING,
            CandidateStatus.ENTRY_READY: ForwardTransition.ENTRY_READY,
            CandidateStatus.INELIGIBLE_FOR_EXECUTION: ForwardTransition.ENTRY_BLOCKED,
        }
        if candidate.setup is not None and candidate.setup.state is SetupState.TRIGGERED:
            terminal = (
                ForwardTransition.ENTRY_READY if signal is not None
                else ForwardTransition.ENTRY_BLOCKED
            )
            if self._last_transition.get(symbol) is terminal:
                return ()
            transitions.append(ForwardTransition.SETUP_TRIGGERED)
            transitions.append(terminal)
        else:
            transitions.append(mapping[candidate.status])
        records: list[CaptureRecord] = []
        for transition in transitions:
            if self._last_transition.get(symbol) is transition:
                continue
            records.append(_transition_record(
                candidate, transition,
                tuple(code.value for code in candidate.reason_codes),
                (
                    _gate_diagnostics(candidate, self.config, account=account)
                    if transition is ForwardTransition.ENTRY_BLOCKED else ()
                ),
            ))
            self._last_transition[symbol] = transition
        return tuple(records)

    def _open_paper(
        self, signal: MomentumEntrySignal, shares: int, risk_dollars: Decimal,
        float_provenance: FloatProvenance,
    ) -> tuple[CaptureRecord, ...]:
        if signal.symbol in self._paper:
            return ()
        if self._paper_entry_submitter is not None and not self._paper_entry_submitter(signal, shares, risk_dollars):
            return ()
        first = int((Decimal(shares) * self.config.trade_management.first_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
        second = int((Decimal(shares) * self.config.trade_management.second_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
        state = _PaperState(signal, signal.entry_trigger, shares, shares, signal.stop_price, first, second)
        self._paper[signal.symbol] = state
        fill = CaptureRecord.create(
            CaptureRecordType.PAPER_FILL, signal.symbol, signal.timestamp,
            {"action": "ENTRY", "setup": signal.setup_type.value,
             "entry_trigger": signal.entry_trigger, "fill_price": signal.entry_trigger,
             "structural_stop": signal.stop_price, "stop_model": signal.stop_model.value,
             "risk_per_share": signal.risk_per_share, "planned_shares": shares,
             "filled_shares": shares, "risk_dollars": risk_dollars,
             "momentum_score": signal.momentum_score, "spread_percent": signal.spread_percent,
             "relative_volume": signal.relative_volume, "float_shares": signal.float_shares,
             "float_provenance": float_provenance.value,
             "price": signal.reference_price,
             "catalyst_state": signal.catalyst_state.value, "session": signal.session,
             "targets": signal.target_levels, "live_execution_authorized": False,
             "authority": "ANALYTICAL_FORWARD_CAPTURE"},
            identity_parts=("ENTRY",),
        )
        transition = CaptureRecord.create(
            CaptureRecordType.STATE_TRANSITION, signal.symbol, signal.timestamp,
            {"from": ForwardTransition.ENTRY_READY.value,
             "to": ForwardTransition.PAPER_ENTRY.value, "reason_codes": []},
            identity_parts=(ForwardTransition.PAPER_ENTRY.value,),
        )
        self._last_transition[signal.symbol] = ForwardTransition.PAPER_ENTRY
        return fill, transition

    @property
    def open_paper_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._paper))

    @property
    def counterfactual_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._counterfactual))

    def _advance_paper(self, state: _PaperState, bar: MinuteBar, observed_at) -> tuple[CaptureRecord, ...]:
        state.last_bar_timestamp = bar.timestamp
        if (
            self._paper_entry_submitter is not None
            and self._paper_position_quantity_source is not None
            and self._paper_position_quantity_source(state.signal.symbol) <= 0
        ):
            return ()
        signal = state.signal
        records: list[CaptureRecord] = []
        if bar.low <= state.stop:
            state.minimum_low = state.stop if state.minimum_low is None else min(state.minimum_low, state.stop)
            state.maximum_high = (
                state.entry_price if state.maximum_high is None else state.maximum_high
            )
            self._submit_exit(state, state.stop, state.remaining, "STOP")
            records.append(self._paper_fill(state, observed_at, "EXIT", "STOP", state.stop, state.remaining))
            state.realized_pnl += (state.stop - state.entry_price) * state.remaining
            state.remaining = 0
        else:
            state.minimum_low = bar.low if state.minimum_low is None else min(state.minimum_low, bar.low)
            state.maximum_high = bar.high if state.maximum_high is None else max(state.maximum_high, bar.high)
            if not state.first_taken and bar.high >= signal.target_levels[0]:
                quantity = min(state.first_quantity, state.remaining)
                self._submit_exit(state, signal.target_levels[0], quantity, "FIRST_TARGET")
                records.append(self._paper_fill(state, observed_at, "PARTIAL", "FIRST_TARGET", signal.target_levels[0], quantity))
                state.realized_pnl += (signal.target_levels[0] - state.entry_price) * quantity
                state.remaining -= quantity
                state.first_taken = True
                state.stop = max(state.stop, state.entry_price)
            if state.remaining and not state.second_taken and bar.high >= signal.target_levels[1]:
                quantity = min(state.second_quantity, state.remaining)
                self._submit_exit(state, signal.target_levels[1], quantity, "SECOND_TARGET")
                records.append(self._paper_fill(state, observed_at, "PARTIAL", "SECOND_TARGET", signal.target_levels[1], quantity))
                state.realized_pnl += (signal.target_levels[1] - state.entry_price) * quantity
                state.remaining -= quantity
                state.second_taken = True
            if state.remaining and bar.high >= signal.target_levels[2]:
                self._submit_exit(state, signal.target_levels[2], state.remaining, "RUNNER_TARGET")
                records.append(self._paper_fill(state, observed_at, "EXIT", "RUNNER_TARGET", signal.target_levels[2], state.remaining))
                state.realized_pnl += (signal.target_levels[2] - state.entry_price) * state.remaining
                state.remaining = 0
        if state.remaining and state.first_taken and state.prior_low is not None and state.prior_low < bar.close:
            state.stop = max(state.stop, state.prior_low)
        state.prior_low = bar.low
        if not state.remaining:
            risk_dollars = signal.risk_per_share * state.initial_quantity
            realized_r = state.realized_pnl / risk_dollars
            mae_r = (state.minimum_low - state.entry_price) / signal.risk_per_share
            mfe_r = (state.maximum_high - state.entry_price) / signal.risk_per_share
            hold_seconds = Decimal(str((bar.timestamp - signal.timestamp).total_seconds()))
            records.append(CaptureRecord.create(
                CaptureRecordType.STATE_TRANSITION, signal.symbol, observed_at,
                {"from": self._last_transition.get(signal.symbol, ForwardTransition.PAPER_ENTRY).value,
                 "to": ForwardTransition.PAPER_EXIT.value,
                 "reason_codes": [], "realized_r": realized_r,
                 "mae_r": mae_r, "mfe_r": mfe_r,
                 "hold_seconds": hold_seconds},
                identity_parts=(ForwardTransition.PAPER_EXIT.value, bar.timestamp.isoformat()),
            ))
            self._last_transition[signal.symbol] = ForwardTransition.PAPER_EXIT
            self._paper.pop(signal.symbol, None)
        elif records:
            records.append(CaptureRecord.create(
                CaptureRecordType.STATE_TRANSITION, signal.symbol, observed_at,
                {"from": self._last_transition.get(signal.symbol, ForwardTransition.PAPER_ENTRY).value,
                 "to": ForwardTransition.PAPER_PARTIAL.value, "reason_codes": []},
                identity_parts=(ForwardTransition.PAPER_PARTIAL.value, bar.timestamp.isoformat()),
            ))
            self._last_transition[signal.symbol] = ForwardTransition.PAPER_PARTIAL
        return tuple(records)

    def _submit_exit(self, state: _PaperState, price: Decimal, quantity: int, reason: str) -> None:
        if self._paper_exit_submitter is not None:
            self._paper_exit_submitter(
                state.signal.symbol, quantity, price, reason,
                lifecycle_identity(state.signal),
            )

    def _paper_fill(self, state: _PaperState, timestamp, action: str, label: str,
                    price: Decimal, quantity: int) -> CaptureRecord:
        return CaptureRecord.create(
            CaptureRecordType.PAPER_FILL, state.signal.symbol, timestamp,
            {"action": action, "label": label, "fill_price": price,
             "filled_shares": quantity, "remaining_before": state.remaining,
             "active_stop": state.stop, "source": "SIMULATED_PAPER",
             "live_execution_authorized": False,
             "authority": "ANALYTICAL_FORWARD_CAPTURE"},
            identity_parts=(action, label, str(state.last_bar_timestamp)),
        )

    def _start_counterfactual(self, candidate: MomentumCandidate) -> tuple[CaptureRecord, ...]:
        setup = candidate.setup
        assert setup is not None and setup.trigger is not None and setup.stop_price is not None
        if candidate.symbol in self._counterfactual:
            return ()
        state = _CounterState(candidate.symbol, candidate.timestamp, setup.trigger, setup.stop_price)
        self._counterfactual[candidate.symbol] = state
        return (CaptureRecord.create(
            CaptureRecordType.COUNTERFACTUAL, candidate.symbol, candidate.timestamp,
            {"action": "START", "setup": setup.setup_type.value,
             "momentum_score": candidate.score.total, "trigger": setup.trigger,
             "stop": setup.stop_price,
             "blocking_gates": _gate_diagnostics(candidate, self.config, account=None),
             "excluded_from_v1_performance": True},
            identity_parts=("START", setup.setup_type.value),
        ),)

    def _recover(self) -> None:
        for record in self.store.records(record_type=CaptureRecordType.MINUTE_BAR):
            try:
                self._seen_bars.add((
                    record.symbol,
                    datetime.fromisoformat(record.payload["bar_timestamp"]),
                ))
            except (KeyError, ValueError):
                continue
        for record in self.store.records(record_type=CaptureRecordType.STATE_TRANSITION):
            payload = record.payload
            try:
                self._last_transition[record.symbol] = ForwardTransition(payload["to"])
            except (KeyError, ValueError):
                continue
        for record in self.store.records(record_type=CaptureRecordType.COUNTERFACTUAL):
            payload = record.payload
            action = payload.get("action")
            if action == "START":
                self._counterfactual[record.symbol] = _CounterState(
                    record.symbol, record.timestamp, Decimal(payload["trigger"]),
                    Decimal(payload["stop"]),
                )
            elif action == "PATH" and record.symbol in self._counterfactual:
                state = self._counterfactual[record.symbol]
                state.bars_observed = int(payload["bars_observed"])
                state.last_bar_timestamp = datetime.fromisoformat(
                    payload["source_bar_timestamp"]
                )
            elif action == "END":
                self._counterfactual.pop(record.symbol, None)
        # Rebuild still-open paper states from immutable fills.
        for record in self.store.records(record_type=CaptureRecordType.PAPER_FILL):
            payload = record.payload
            if payload.get("action") == "ENTRY":
                signal = _signal_from_entry(record, payload)
                quantity = int(payload["filled_shares"])
                first = int((Decimal(quantity) * self.config.trade_management.first_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
                second = int((Decimal(quantity) * self.config.trade_management.second_target_exit_percent).to_integral_value(rounding=ROUND_FLOOR))
                self._paper[record.symbol] = _PaperState(
                    signal, Decimal(payload["fill_price"]), quantity, quantity,
                    Decimal(payload["structural_stop"]), first, second,
                )
            elif record.symbol in self._paper:
                state = self._paper[record.symbol]
                quantity = int(payload["filled_shares"])
                price = Decimal(payload["fill_price"])
                state.realized_pnl += (price - state.entry_price) * quantity
                state.remaining -= quantity
                label = payload.get("label")
                state.first_taken |= label == "FIRST_TARGET"
                state.second_taken |= label == "SECOND_TARGET"
                if state.first_taken:
                    state.stop = max(state.stop, state.entry_price)
                if state.remaining <= 0:
                    self._paper.pop(record.symbol, None)


def _bar_record(bar: MinuteBar, observed_at: datetime) -> CaptureRecord:
    return CaptureRecord.create(
        CaptureRecordType.MINUTE_BAR, bar.symbol, observed_at,
        {"bar_timestamp": bar.timestamp, "interval": "1m", "completed": True,
         "completion_timestamp": bar.timestamp + timedelta(minutes=1),
         "observation_timestamp": observed_at,
         "open": bar.open, "high": bar.high, "low": bar.low,
         "close": bar.close, "volume": bar.volume},
        identity_parts=(bar.timestamp.isoformat(),),
    )


def _discovery_record(value: PointInTimeObservation, candidate: MomentumCandidate) -> CaptureRecord:
    observation = value.observation
    spread = candidate.spread_percent
    return CaptureRecord.create(
        CaptureRecordType.DISCOVERY, candidate.symbol, candidate.timestamp,
        {"session": candidate.session, "last_price": candidate.price,
         "percentage_change": candidate.percentage_change,
         "bid": observation.bid, "ask": observation.ask, "spread_percent": spread,
         "volume": candidate.volume, "relative_volume": candidate.relative_volume,
         "average_volume": observation.average_30_day_volume,
         "dollar_volume": candidate.dollar_volume, "float_value": candidate.float_shares,
         "float_provenance": value.float_provenance.value,
         "catalyst_state": candidate.catalyst_status.value,
         "catalyst_type": candidate.catalyst_type.value,
         "catalyst_timestamp": value.catalyst_event_timestamp,
         "catalyst_date": value.catalyst_event_date,
         "catalyst_source": value.catalyst_source,
         "tradable": candidate.tradable, "halted": candidate.halted,
         "halt_state_known": value.halt_state_known,
         "momentum_score": candidate.score.total,
         "stocks_in_play": tuple(item.value for item in candidate.stocks_in_play)},
    )


def _decision_record(value, candidate, completed, features) -> CaptureRecord:
    observation = value.observation
    setup = candidate.setup
    payload = {
        "decision_timestamp": candidate.timestamp,
        "observation": {
            "price": observation.price, "previous_close": observation.previous_close,
            "current_volume": observation.current_volume,
            "average_30_day_volume": observation.average_30_day_volume,
            "float_shares": observation.float_shares, "bid": observation.bid,
            "ask": observation.ask, "catalyst": observation.catalyst.value,
            "catalyst_status": observation.catalyst_status.value,
            "tradable": observation.tradable, "halted": observation.halted,
            "asset_class": observation.asset_class.value,
        },
        "session": value.session,
        "bar_timestamps": tuple(bar.timestamp for bar in completed),
        "features": None if features is None else {
            "vwap": features.vwap, "session_high": features.session_high,
            "session_low": features.session_low, "rolling_high": features.rolling_high,
            "rolling_low": features.rolling_low,
            "rolling_change_percent": features.rolling_change_percent,
            "rolling_volume": features.rolling_volume,
            "volume_acceleration": features.volume_acceleration,
            "distance_from_vwap_percent": features.distance_from_vwap_percent,
            "distance_from_hod_percent": features.distance_from_hod_percent,
            "pullback_depth_percent": features.pullback_depth_percent,
            "consolidation_duration": features.consolidation_duration,
            "breakout_level": features.breakout_level,
            "breakout_volume_ratio": features.breakout_volume_ratio,
        },
        "score": candidate.score.total,
        "score_components": candidate.score.components,
        "stocks_in_play": tuple(item.value for item in candidate.stocks_in_play),
        "status": candidate.status.value,
        "setup": None if setup is None else {
            "type": setup.setup_type.value, "state": setup.state.value,
            "score": setup.score, "trigger": setup.trigger,
            "stop_price": setup.stop_price,
            "stop_model": None if setup.stop_model is None else setup.stop_model.value,
            "resistance": setup.resistance,
        },
        "reason_codes": tuple(code.value for code in candidate.reason_codes),
    }
    return CaptureRecord.create(CaptureRecordType.DECISION, candidate.symbol,
                                candidate.timestamp, payload)


def _transition_record(candidate, transition, reasons, gates) -> CaptureRecord:
    return CaptureRecord.create(
        CaptureRecordType.STATE_TRANSITION, candidate.symbol, candidate.timestamp,
        {"to": transition.value, "reason_codes": reasons,
         "blocking_gates": tuple(gate for gate in gates if not gate["passed"])},
        identity_parts=(transition.value,),
    )


def _quality_record(value, completed, capture_config) -> CaptureRecord:
    observation = value.observation
    flags = {
        "missing_bid_ask": observation.bid is None or observation.ask is None,
        "stale_bid_ask": (
            value.quote_freshness_seconds is None
            or value.quote_freshness_seconds > capture_config.quote_stale_after_seconds
        ),
        "missing_catalyst": observation.catalyst_status.value in {"UNKNOWN", "UNAVAILABLE"},
        "unknown_catalyst": observation.catalyst_status.value == "UNKNOWN",
        "unavailable_catalyst": observation.catalyst_status.value == "UNAVAILABLE",
        "missing_float": observation.float_shares is None,
        "proxy_float": value.float_provenance is FloatProvenance.MARKET_CAP_PRICE_PROXY,
        "missing_volume": not value.volume_known,
        "missing_historical_bars": not value.historical_bars_available or not completed,
        "halt_uncertainty": not value.halt_state_known,
    }
    return CaptureRecord.create(CaptureRecordType.DATA_QUALITY, observation.symbol,
                                observation.timestamp, flags)


def _gate_diagnostics(candidate, config, account):
    setup = candidate.setup
    risk = None
    if setup is not None and setup.trigger is not None and setup.stop_price is not None:
        risk = setup.trigger - setup.stop_price
    return (
        {"gate": "momentum_score", "passed": candidate.score.total >= config.entry.minimum_momentum_score,
         "observed": candidate.score.total, "limit": config.entry.minimum_momentum_score},
        {"gate": "setup", "passed": setup is not None and setup.state is SetupState.TRIGGERED,
         "observed": None if setup is None else setup.state.value, "limit": SetupState.TRIGGERED.value},
        {"gate": "spread", "passed": candidate.spread_percent is not None and candidate.spread_percent <= config.entry.maximum_spread_percent,
         "observed": candidate.spread_percent, "limit": config.entry.maximum_spread_percent},
        {"gate": "catalyst", "passed": not config.entry.require_catalyst_for_entry or candidate.catalyst_status.value == "TRUE",
         "observed": candidate.catalyst_status.value, "limit": "TRUE"},
        {"gate": "liquidity", "passed": candidate.dollar_volume >= config.entry.minimum_dollar_volume,
         "observed": candidate.dollar_volume, "limit": config.entry.minimum_dollar_volume},
        {"gate": "tradability", "passed": candidate.tradable, "observed": candidate.tradable, "limit": True},
        {"gate": "halt", "passed": not candidate.halted, "observed": candidate.halted, "limit": False},
        {"gate": "session", "passed": candidate.session in config.entry.allowed_sessions,
         "observed": candidate.session, "limit": tuple(sorted(config.entry.allowed_sessions))},
        {"gate": "risk_distance", "passed": risk is not None and ZERO < risk <= config.entry.maximum_risk_per_share,
         "observed": risk, "limit": config.entry.maximum_risk_per_share},
        {"gate": "paper_risk_context", "passed": account is not None and account.risk_engine_approved,
         "observed": None if account is None else account.risk_engine_approved, "limit": True},
    )


def _account_gate_diagnostics(signal, account):
    return (
        {"gate": "allowed_symbols", "passed": signal.symbol in account.allowed_symbols,
         "observed": signal.symbol in account.allowed_symbols, "limit": True},
        {"gate": "risk_engine", "passed": account.risk_engine_approved,
         "observed": account.risk_engine_approved, "limit": True},
        {"gate": "broker_restriction", "passed": not account.broker_restriction,
         "observed": account.broker_restriction, "limit": False},
        {"gate": "buying_power", "passed": account.buying_power >= signal.entry_trigger,
         "observed": account.buying_power, "limit": signal.entry_trigger},
        {"gate": "exposure", "passed": (
             account.exposure_limit is None
             or account.existing_exposure < account.exposure_limit
         ), "observed": account.existing_exposure, "limit": account.exposure_limit},
    )


def _signal_from_entry(record, payload) -> MomentumEntrySignal:
    from app.momentum_scanner.models import CatalystStatus
    from .models import SetupType, StopModel
    return MomentumEntrySignal(
        "WARRIOR_MOMENTUM_V1", record.symbol, record.timestamp, payload["session"],
        Decimal(payload["momentum_score"]), SetupType(payload["setup"]),
        Decimal(payload["entry_trigger"]), Decimal(payload["fill_price"]),
        Decimal(payload["structural_stop"]), StopModel(payload["stop_model"]),
        Decimal(payload["risk_per_share"]), tuple(Decimal(item) for item in payload["targets"]),
        CatalystStatus(payload["catalyst_state"]), Decimal(payload["relative_volume"]),
        None if payload.get("float_shares") is None else Decimal(payload["float_shares"]),
        None if payload.get("spread_percent") is None else Decimal(payload["spread_percent"]),
        ZERO, ZERO, Decimal("0"), (), False,
    )


__all__ = ["WarriorForwardCaptureService"]

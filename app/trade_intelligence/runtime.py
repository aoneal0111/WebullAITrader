"""Autonomous, non-executable runtime observation coordinator.

The coordinator owns only bounded in-memory episode state. All durable work is
submitted with nonblocking calls to :class:`TradeIntelligenceService`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Callable

from app.live_scanner.session import scanner_session
from app.market.calendar import EASTERN
from app.market_data.models import MarketEvent, MarketEventType, TradePayload
from app.momentum_scanner.models import ScannerDecision
from app.performance_diagnostics import performance_diagnostics
from app.strategies.warrior_momentum.forward_models import PointInTimeObservation
from app.strategies.warrior_momentum.models import MinuteBar, MomentumCandidate, SetupState

from .features import extract_completed_bar_features
from .models import (
    AtlasDecision, DecisionObservation, DecisionTimeSnapshot, FEATURE_VERSION,
    OpportunityKey, PaperExecutionObservation, PriceBar,
    TradeOpportunityExperience, WorkerMetrics,
)
from .service import DEFAULT_STORE_PATH, TradeIntelligenceService
from .warrior_adapter import from_warrior_candidate


@dataclass(slots=True)
class _Episode:
    episode_id: str
    experience_id: str
    symbol: str
    session: str
    session_date: date
    started_at: datetime
    last_signature: tuple[object, ...]
    setup_anchor: tuple[object, ...] | None = None
    lifecycle_identity: str | None = None
    parent_admitted: bool = False


@dataclass(slots=True)
class _BarState:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class TradeIntelligenceRuntimeObserver:
    """Correlate authoritative scanner/Warrior/PAPER publications.

    It deliberately exposes no result that scanner, Warrior, risk, PAPER, or
    LIVE can consult. Admission failures are counted by the research service and
    never propagated into production control flow.
    """

    def __init__(
        self, *, enabled: bool, environment: str,
        path: str | Path = DEFAULT_STORE_PATH, capacity: int = 4096,
        service_factory: Callable[..., TradeIntelligenceService] = TradeIntelligenceService,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.enabled = bool(enabled) and environment.strip().upper() in {"TEST", "PAPER"}
        self.environment = environment.strip().upper()
        self.path = Path(path)
        self.capacity = capacity
        self._factory = service_factory
        self._clock = clock
        self._service: TradeIntelligenceService | None = None
        self._last_metrics: WorkerMetrics | None = None
        self._episodes: dict[str, _Episode] = {}
        self._bars: dict[str, _BarState] = {}
        self._lock = RLock()
        performance_diagnostics.set_trade_intelligence_enabled(self.enabled)

    def start(self, environment: str | None = None) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._service is None:
                self._service = self._factory(self.path, capacity=self.capacity)

    def stop(self, *, timeout_seconds: float = 10.0) -> bool:
        with self._lock:
            service = self._service
            self._episodes.clear()
            self._bars.clear()
        if service is None:
            return True
        closed = service.close(timeout_seconds=timeout_seconds)
        self._last_metrics = service.metrics()
        performance_diagnostics.update_trade_intelligence(self._last_metrics)
        if closed:
            with self._lock:
                if self._service is service:
                    self._service = None
        return closed

    def __call__(self, event: MarketEvent) -> None:
        # Session/reset events terminate producer-owned correlation only. Quotes
        # and trades never create experience rows here.
        if not self.enabled:
            return
        if event.event_type is MarketEventType.SESSION_CHANGE:
            if event.symbol is None:
                with self._lock:
                    self._episodes.clear()
            else:
                self.reset_symbol(event.symbol)
            return
        if (
            event.symbol is not None
            and event.event_type is MarketEventType.TRADE
            and isinstance(event.payload, TradePayload)
        ):
            self._observe_trade_bar(event)

    def observe_scanner_decision(self, decision: ScannerDecision) -> None:
        if not self.enabled or not (
            decision.qualified or decision.technical_qualifies_without_catalyst
        ):
            return
        fast_signature = (
            "SCANNER",
            AtlasDecision.WATCHING.value if decision.qualified else AtlasDecision.REJECTED.value,
            tuple(decision.failed_rules),
        )
        with self._lock:
            current = self._episodes.get(decision.symbol.strip().upper())
            if current is not None and current.last_signature == fast_signature:
                return
        try:
            self._observe_scanner(decision)
        except Exception:
            # Research construction cannot degrade scanner publication.
            return

    def observe_warrior_decision(
        self, value: PointInTimeObservation, candidate: MomentumCandidate,
        signal: object | None = None,
    ) -> None:
        if not self.enabled or candidate.setup is None:
            return
        if candidate.setup.state not in {SetupState.FORMING, SetupState.TRIGGERED}:
            return
        try:
            self._observe_warrior(value, candidate, signal)
        except Exception:
            return

    def observe_completed_bar(self, bar: MinuteBar) -> None:
        service = self._service
        if not self.enabled or service is None:
            return
        try:
            service.observe_completed_bar(PriceBar(
                bar.symbol, bar.timestamp, bar.open, bar.high, bar.low,
                bar.close, bar.volume,
            ))
            self._publish_metrics()
        except Exception:
            return

    def observe_paper_fact(
        self, *, observation_id: str, observed_at: datetime, event_type: str,
        symbol: str, order_id: str | None = None, fill_id: str | None = None,
        side: str | None = None, price: Decimal | None = None,
        quantity: Decimal | None = None, strategy_lifecycle_id: str | None = None,
    ) -> None:
        service = self._service
        if not self.enabled or service is None:
            return
        normalized = symbol.strip().upper()
        with self._lock:
            symbol_candidates = tuple(
                item for item in self._episodes.values()
                if item.symbol == normalized
            )
            exact = tuple(
                item for item in symbol_candidates
                if strategy_lifecycle_id is not None
                and item.lifecycle_identity == strategy_lifecycle_id
            )
            # PAPER publication can occur synchronously inside Warrior observe,
            # before the returned signal alias is attached. A sole active symbol
            # episode is still deterministic; multiple candidates stay ambiguous.
            candidates = exact or symbol_candidates
        status = "CORRELATED" if len(candidates) == 1 else (
            "AMBIGUOUS" if len(candidates) > 1 else "UNRESOLVED"
        )
        experience_id = candidates[0].experience_id if status == "CORRELATED" else None
        try:
            service.observe_paper_execution(PaperExecutionObservation(
                observation_id=observation_id, observed_at=observed_at,
                event_type=event_type, symbol=normalized,
                experience_id=experience_id, correlation_status=status,
                order_id=order_id, fill_id=fill_id, side=side, price=price,
                quantity=quantity, strategy_lifecycle_id=strategy_lifecycle_id,
            ))
            self._publish_metrics()
        except Exception:
            return

    def reset_symbol(self, symbol: str) -> None:
        with self._lock:
            normalized = symbol.strip().upper()
            self._episodes.pop(normalized, None)
            self._bars.pop(normalized, None)

    def metrics(self) -> WorkerMetrics | None:
        service = self._service
        return self._last_metrics if service is None else service.metrics()

    def retained_symbols(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._episodes))

    def _observe_scanner(self, decision: ScannerDecision) -> None:
        service = self._service
        cutoff = decision.observed_at or decision.timestamp
        if service is None or cutoff is None:
            return
        cutoff = _utc(cutoff)
        source_time = None if decision.timestamp is None else _utc(decision.timestamp)
        session = scanner_session(source_time or cutoff).value
        with self._lock:
            previous = self._episodes.get(decision.symbol.upper())
        new_episode = previous is None or previous.session != session or previous.session_date != cutoff.astimezone(EASTERN).date()
        key = self._episode(decision.symbol, session, cutoff, decision.source_event_identity or "scanner")
        atlas_decision = AtlasDecision.WATCHING if decision.qualified else AtlasDecision.REJECTED
        signature = ("SCANNER", atlas_decision.value, tuple(decision.failed_rules))
        with self._lock:
            if not new_episode and key.last_signature == signature:
                return
        snapshot = DecisionTimeSnapshot(
            decision_timestamp=cutoff, source_timestamp=source_time,
            last_price=decision.price, bid=decision.bid, ask=decision.ask,
            spread_percent=decision.metrics.spread_percent,
            percentage_change=decision.metrics.percentage_change,
            current_volume=decision.current_volume,
            average_volume=decision.average_30_day_volume,
            relative_volume=decision.metrics.relative_volume,
            dollar_volume=decision.metrics.dollar_volume,
            float_shares=decision.float_shares, tradable=decision.tradable,
            halted=decision.halted,
            quote_freshness_seconds=_age(cutoff, decision.quote_timestamp),
            trade_freshness_seconds=_age(cutoff, decision.last_price_timestamp),
            catalyst_status=decision.catalyst_status.value,
            catalyst_type=decision.catalyst.value,
            catalyst_source_identity=decision.catalyst_source_url or decision.catalyst_source,
            scanner_qualified=decision.qualified, scanner_score=Decimal(decision.score),
            scanner_rank=decision.scanner_rank, passed_rules=decision.passed_rules,
            failed_rules=decision.failed_rules,
        )
        experience = TradeOpportunityExperience(
            key=OpportunityKey("WARRIOR_MOMENTUM_V1", decision.symbol,
                               cutoff.astimezone(EASTERN).date(), session, key.episode_id),
            environment=self.environment, policy_version=decision.policy_version,
            strategy_version="WARRIOR_MOMENTUM_V1", model_version="NONE",
            feature_version=FEATURE_VERSION,
            source_event_identity=decision.source_event_identity or f"scanner:{decision.symbol}:{cutoff.isoformat()}",
            snapshot=snapshot, atlas_decision=atlas_decision,
            blockers=tuple(decision.failed_rules), technically_actionable=False,
        )
        if key.experience_id != experience.experience_id:
            key.experience_id = experience.experience_id
        if not self._ensure_parent(key, experience):
            return
        self._append_decision(key, snapshot, atlas_decision, tuple(decision.failed_rules),
                              signature, "SCANNER_QUALIFICATION")

    def _observe_warrior(self, value, candidate, signal) -> None:
        service = self._service
        if service is None:
            return
        cutoff = _utc(candidate.timestamp)
        setup = candidate.setup
        assert setup is not None
        anchor = (setup.setup_type.value, setup.trigger, setup.stop_price)
        with self._lock:
            current = self._episodes.get(candidate.symbol.upper())
            if current is not None and current.setup_anchor is not None and current.setup_anchor != anchor:
                self._episodes.pop(candidate.symbol.upper(), None)
                current = None
        episode = self._episode(candidate.symbol, candidate.session, cutoff,
                                f"warrior:{candidate.symbol}:{cutoff.isoformat()}:{anchor}")
        bars = tuple(PriceBar(item.symbol, item.timestamp, item.open, item.high,
                              item.low, item.close, item.volume) for item in value.bars)
        feature_values, feature_sources = extract_completed_bar_features(
            bars, decision_cutoff=cutoff,
        )
        experience = from_warrior_candidate(
            value, candidate, episode_id=episode.episode_id,
            source_event_identity=f"warrior:{candidate.symbol}:{cutoff.isoformat()}:{setup.state.value}",
            environment=self.environment, strategy_version="WARRIOR_MOMENTUM_V1",
            model_version="NONE", actually_traded=False,
        )
        snapshot = replace(experience.snapshot, features=feature_values,
                           feature_source_timestamps=feature_sources)
        experience = replace(experience, snapshot=snapshot)
        episode.experience_id = experience.experience_id
        episode.setup_anchor = anchor
        episode.lifecycle_identity = _signal_identity(signal)
        if not self._ensure_parent(episode, experience):
            return
        blockers = tuple(dict.fromkeys(item.value for item in candidate.reason_codes))
        stage = "ENTRY_READY" if signal is not None else setup.state.value
        signature = (stage, blockers, anchor)
        self._append_decision(episode, snapshot, experience.atlas_decision,
                              blockers, signature, f"WARRIOR_{stage}",
                              technically_actionable=experience.technically_actionable,
                              actually_traded=False)

    def _episode(self, symbol: str, session: str, cutoff: datetime, source: str) -> _Episode:
        normalized = symbol.strip().upper()
        session_date = cutoff.astimezone(EASTERN).date()
        with self._lock:
            current = self._episodes.get(normalized)
            if current is not None and current.session == session and current.session_date == session_date:
                return current
            episode_id = sha256(
                f"runtime-episode-v1|{normalized}|{session_date}|{session}|{source}".encode()
            ).hexdigest()
            experience_id = OpportunityKey(
                "WARRIOR_MOMENTUM_V1", normalized, session_date, session, episode_id,
            ).experience_id
            current = _Episode(episode_id, experience_id, normalized, session,
                               session_date, cutoff, ())
            self._episodes[normalized] = current
            return current

    def _ensure_parent(
        self, episode: _Episode, experience: TradeOpportunityExperience,
    ) -> bool:
        """Admit the immutable parent before any child can be accepted.

        Episode allocation intentionally remains producer-local. Construction
        can fail after allocation (for example on an anti-lookahead bar), so an
        allocated episode is not evidence that its parent reached the service.
        The nonblocking submit is serialized by the episode lock and retried by
        a later valid observation when bounded admission previously failed.
        """

        service = self._service
        if service is None:
            return False
        with self._lock:
            if episode.parent_admitted:
                return True
            admitted = service.submit_experience(experience)
            if admitted:
                episode.parent_admitted = True
            return admitted

    def _append_decision(
        self, episode: _Episode, snapshot: DecisionTimeSnapshot,
        decision: AtlasDecision, blockers: tuple[str, ...], signature: tuple[object, ...],
        stage: str, *, technically_actionable: bool = False,
        actually_traded: bool = False,
    ) -> None:
        service = self._service
        with self._lock:
            if signature == episode.last_signature:
                return
            episode.last_signature = signature
        if service is not None:
            service.submit_decision(DecisionObservation(
                experience_id=episode.experience_id,
                observed_at=snapshot.decision_timestamp,
                source_event_identity=f"{stage}:{snapshot.decision_timestamp.isoformat()}",
                atlas_decision=decision, snapshot=snapshot, blockers=blockers,
                technically_actionable=technically_actionable,
                actually_traded=actually_traded, symbol=episode.symbol,
                lifecycle_stage=stage,
            ))
            self._publish_metrics()

    def _observe_trade_bar(self, event: MarketEvent) -> None:
        assert event.symbol is not None and isinstance(event.payload, TradePayload)
        symbol = event.symbol.strip().upper()
        minute = _utc(event.timestamp).replace(second=0, microsecond=0)
        price, size = event.payload.price, max(Decimal("0"), event.payload.size)
        completed = None
        with self._lock:
            current = self._bars.get(symbol)
            if current is not None and minute < current.timestamp:
                return
            if current is not None and minute > current.timestamp:
                completed = PriceBar(
                    symbol, current.timestamp, current.open, current.high,
                    current.low, current.close, current.volume,
                )
                current = None
            if current is None:
                self._bars[symbol] = _BarState(
                    symbol, minute, price, price, price, price, size,
                )
            else:
                current.high = max(current.high, price)
                current.low = min(current.low, price)
                current.close = price
                current.volume += size
        with self._lock:
            needs_outcome = any(
                item.symbol == symbol
                and completed is not None
                and completed.timestamp <= item.started_at + timedelta(minutes=30)
                for item in self._episodes.values()
            )
        if completed is not None and needs_outcome and self._service is not None:
            self._service.observe_completed_bar(completed)
            self._publish_metrics()

    def _publish_metrics(self) -> None:
        service = self._service
        if service is not None:
            performance_diagnostics.update_trade_intelligence(service.metrics())


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("runtime observation timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _age(cutoff: datetime, source: datetime | None) -> Decimal | None:
    if source is None:
        return None
    return Decimal(str(max(0, (cutoff - _utc(source)).total_seconds())))


def _signal_identity(signal: object | None) -> str | None:
    if signal is None:
        return None
    values = (
        getattr(signal, "strategy_id", ""), getattr(signal, "symbol", ""),
        getattr(signal, "timestamp", ""), getattr(signal, "setup_type", ""),
        getattr(signal, "entry_trigger", ""), getattr(signal, "stop_price", ""),
    )
    return "|".join(str(getattr(value, "value", value)) for value in values)

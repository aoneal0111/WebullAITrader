from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.momentum_scanner.models import (
    CatalystStatus, CatalystType, ScannerDecision, ScannerMetrics,
)
from app.trade_intelligence.runtime import TradeIntelligenceRuntimeObserver
from tests.warrior_momentum.test_shadow_analysis import candidate as warrior_candidate
from tests.warrior_momentum.test_shadow_analysis import point as warrior_point
from app.strategies.warrior_momentum import (
    CandidateStatus, SetupDetection, SetupState, SetupType, StopModel,
)


T0 = datetime(2026, 8, 31, 14, 30, tzinfo=UTC)


class RecordingService:
    def __init__(self, path, *, capacity):
        self.experiences = []
        self.decisions = []
        self.bars = []
        self.paper = []
        self.closed = False

    def submit_experience(self, value):
        self.experiences.append(value)
        return True

    def submit_decision(self, value):
        self.decisions.append(value)
        return True

    def observe_completed_bar(self, value):
        self.bars.append(value)
        return True

    def observe_paper_execution(self, value):
        self.paper.append(value)
        return True

    def close(self, *, timeout_seconds):
        self.closed = True
        return True

    def metrics(self):
        return None


def decision(*, at=T0, qualified=True, failed=()):
    return ScannerDecision(
        symbol="ABCD", qualified=qualified, score=90,
        metrics=ScannerMetrics(Decimal("25"), Decimal("8"),
                               Decimal("1000000"), Decimal("0.2")),
        passed_rules=("price_range", "relative_volume"), failed_rules=failed,
        timestamp=at, observed_at=at + timedelta(milliseconds=10),
        price=Decimal("10"), current_volume=Decimal("100000"),
        average_30_day_volume=Decimal("12500"), float_shares=Decimal("4000000"),
        bid=Decimal("9.99"), ask=Decimal("10.01"), tradable=True, halted=False,
        catalyst=CatalystType.EARNINGS, catalyst_status=CatalystStatus.TRUE,
        technical_qualifies_without_catalyst=True, scanner_rank=1,
        source_event_identity=f"feed:{at.isoformat()}", source_event_type="TRADE",
        last_price_timestamp=at, quote_timestamp=at,
    )


def observer():
    holder = {}

    def factory(path, *, capacity):
        holder["service"] = RecordingService(path, capacity=capacity)
        return holder["service"]

    value = TradeIntelligenceRuntimeObserver(
        enabled=True, environment="TEST", service_factory=factory,
    )
    value.start()
    return value, holder["service"]


def test_thousands_of_scanner_updates_remain_one_experience():
    runtime, service = observer()
    first = decision()
    for index in range(2_000):
        runtime.observe_scanner_decision(replace(
            first, score=90 + index % 3, scanner_rank=1 + index % 20,
            bid=Decimal("9.98") + Decimal(index % 2) / 100,
            ask=Decimal("10.02") + Decimal(index % 2) / 100,
            source_event_identity=f"feed:{index}",
        ))
    assert len(service.experiences) == 1
    assert len(service.decisions) == 1
    assert runtime.retained_symbols() == ("ABCD",)


def test_blocker_transition_appends_without_mutating_initial_snapshot():
    runtime, service = observer()
    runtime.observe_scanner_decision(decision())
    original = service.experiences[0]
    runtime.observe_scanner_decision(decision(qualified=False, failed=("news_catalyst",)))
    assert len(service.experiences) == 1
    assert [item.atlas_decision.value for item in service.decisions] == ["WATCHING", "REJECTED"]
    assert original.snapshot.failed_rules == ()
    assert service.decisions[-1].snapshot.failed_rules == ("news_catalyst",)


def test_reset_and_new_session_create_new_episode():
    runtime, service = observer()
    runtime.observe_scanner_decision(decision())
    runtime.reset_symbol("ABCD")
    runtime.observe_scanner_decision(decision(at=T0 + timedelta(days=1)))
    assert len(service.experiences) == 2
    assert service.experiences[0].experience_id != service.experiences[1].experience_id


def test_paper_correlation_is_observational_and_ambiguity_is_explicit():
    runtime, service = observer()
    runtime.observe_scanner_decision(decision())
    runtime.observe_paper_fact(
        observation_id="paper:1", observed_at=T0 + timedelta(seconds=5),
        event_type="ORDER_ACCEPTED", symbol="ABCD", order_id="order-1",
    )
    assert service.paper[0].correlation_status == "CORRELATED"
    assert service.paper[0].experience_id == service.experiences[0].experience_id
    runtime.observe_paper_fact(
        observation_id="paper:2", observed_at=T0 + timedelta(seconds=6),
        event_type="ORDER_ACCEPTED", symbol="WXYZ", order_id="order-2",
    )
    assert service.paper[1].correlation_status == "UNRESOLVED"
    assert not any(hasattr(runtime, name) for name in ("place_order", "authorize_order", "veto_order"))


def test_scanner_forming_and_triggered_states_share_one_experience():
    runtime, service = observer()
    runtime.observe_scanner_decision(replace(decision(), symbol="XYZ"))
    forming_setup = SetupDetection(
        SetupType.MICRO_PULLBACK, SetupState.FORMING, Decimal("75"),
        trigger=Decimal("10.10"), stop_price=Decimal("9.80"),
        stop_model=StopModel.MICRO_PULLBACK_LOW,
    )
    runtime.observe_warrior_decision(
        warrior_point(timestamp=T0),
        replace(
            warrior_candidate(setup=forming_setup, timestamp=T0),
            status=CandidateStatus.SETUP_FORMING,
        ),
    )
    triggered_setup = replace(forming_setup, state=SetupState.TRIGGERED)
    runtime.observe_warrior_decision(
        warrior_point(timestamp=T0 + timedelta(minutes=1)),
        warrior_candidate(setup=triggered_setup, timestamp=T0 + timedelta(minutes=1)),
    )
    assert len(service.experiences) == 1
    assert [item.lifecycle_stage for item in service.decisions] == [
        "SCANNER_QUALIFICATION", "WARRIOR_FORMING", "WARRIOR_TRIGGERED",
    ]
    assert service.experiences[0].snapshot.setup_state is None
    assert service.decisions[-1].snapshot.setup_state == "TRIGGERED"

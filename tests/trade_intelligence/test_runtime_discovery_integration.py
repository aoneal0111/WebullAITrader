from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.opportunity_discovery import PositionFocusTier
from app.strategies.warrior_momentum import MinuteBar
from app.trade_intelligence import ExperienceStore, TradeIntelligenceService
from app.trade_intelligence.discovery_runtime import (
    DiscoveryTelemetry, KnownDiscoveryContext,
)
from app.trade_intelligence.models import WorkerMetrics
from app.trade_intelligence.runtime import TradeIntelligenceRuntimeObserver


T0 = datetime(2026, 9, 3, 13, 30, tzinfo=UTC)


def _bar(index: int, *, symbol: str = "ABCD") -> MinuteBar:
    price = Decimal("10") + Decimal(index) / Decimal("100")
    return MinuteBar(
        symbol, T0 + timedelta(minutes=index), price,
        price + Decimal("0.20"), price - Decimal("0.10"),
        price + Decimal("0.10"), Decimal("1000") + index,
    )


class _RecordingService:
    def __init__(self, path, *, capacity):
        self.discovery = []
        self.bars = []

    def submit_discovery_observation(self, value):
        self.discovery.append(value)
        return True

    def observe_completed_bar(self, value):
        self.bars.append(value)
        return True

    def metrics(self):
        return WorkerMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, True)

    def discovery_telemetry(self):
        return DiscoveryTelemetry()

    def discovery_context(self, symbol, cutoff):
        return KnownDiscoveryContext(
            symbol, cutoff - timedelta(seconds=1), "opportunity-known",
            ("HIGHER_LOW_CONTINUATION", "HIGH_OF_DAY_BREAKOUT"),
        )

    def close(self, *, timeout_seconds):
        return True


def _observer():
    holder = {}

    def factory(path, *, capacity):
        holder["service"] = _RecordingService(path, capacity=capacity)
        return holder["service"]

    observer = TradeIntelligenceRuntimeObserver(
        enabled=True, environment="TEST", service_factory=factory,
    )
    observer.start()
    return observer, holder["service"]


def test_completed_bar_cadence_is_bounded_and_duplicate_safe():
    observer, service = _observer()
    for index in range(70):
        observer.observe_completed_bar(_bar(index))
    observer.observe_completed_bar(_bar(69))
    assert len(service.discovery) == 70
    assert len(service.discovery[-1].context.completed_bars) == 64
    assert service.discovery[-1].context.completed_bars[0].completed_at == T0 + timedelta(minutes=7)
    assert all(
        item.completed_at <= service.discovery[-1].context.decision_cutoff
        for item in service.discovery[-1].context.completed_bars
    )
    assert service.discovery[-1].context.capabilities.authoritative_vwap is False


def test_eov_context_exposes_only_observational_discovery_memberships():
    observer, _service = _observer()
    cutoff = T0 + timedelta(minutes=1)
    context = observer.entry_opportunity_context(
        "ABCD", "WARRIOR_MOMENTUM_V1|ABCD|episode", cutoff,
    )
    assert context == {
        "observed_at": cutoff - timedelta(seconds=1),
        "opportunity_id": "opportunity-known",
        "detector_memberships": (
            "HIGHER_LOW_CONTINUATION", "HIGH_OF_DAY_BREAKOUT",
        ),
    }


def test_authoritative_position_and_working_order_retention_priority():
    observer, service = _observer()
    positions = SimpleNamespace(positions=(SimpleNamespace(
        account_id="paper", symbol="ABCD", quantity="100", average_cost="10",
    ),))
    orders = SimpleNamespace(orders=(
        SimpleNamespace(order_id="work-1", symbol="ABCD", status="WORKING"),
        SimpleNamespace(order_id="work-2", symbol="WXYZ", status="SUBMITTED"),
    ))
    observer.bind_authoritative_focus_sources(
        position_source=lambda: positions, order_source=lambda: orders,
    )
    observer.observe_completed_bar(_bar(0))
    assert observer.retained_symbols() == ("ABCD", "WXYZ")
    assert service.discovery[-1].focus_tier is PositionFocusTier.OPEN_POSITION
    assert service.discovery[-1].authoritative_position is not None
    assert service.discovery[-1].working_order_ids == ("work-1",)

    positions.positions = ()
    observer.observe_completed_bar(_bar(0, symbol="WXYZ"))
    assert service.discovery[-1].focus_tier is PositionFocusTier.WORKING_ORDER
    assert service.discovery[-1].authoritative_position is None


def test_scanner_exit_and_rank_have_no_position_ownership_authority():
    observer, service = _observer()
    positions = SimpleNamespace(positions=(SimpleNamespace(
        account_id="paper", symbol="ABCD", quantity="25", average_cost="10",
    ),))
    observer.bind_authoritative_focus_sources(
        position_source=lambda: positions,
        order_source=lambda: SimpleNamespace(orders=()),
    )
    for index in range(20):
        observer.observe_completed_bar(_bar(index))
    identities = {
        item.authoritative_position.position_id for item in service.discovery
        if item.authoritative_position is not None
    }
    assert identities and len(identities) == 1
    assert observer.retained_symbols() == ("ABCD",)


def test_worker_persists_append_only_discovery_and_conserves_work(tmp_path: Path):
    path = tmp_path / "phase2a2.sqlite3"
    service = TradeIntelligenceService(path, capacity=64)
    observer = TradeIntelligenceRuntimeObserver(
        enabled=True, environment="TEST",
        service_factory=lambda _path, *, capacity: service,
    )
    observer.start()
    observer.bind_authoritative_focus_sources(
        position_source=lambda: SimpleNamespace(positions=(SimpleNamespace(
            account_id="paper", symbol="ABCD", quantity="100", average_cost="10",
        ),)),
        order_source=lambda: SimpleNamespace(orders=()),
    )
    for index in range(20):
        observer.observe_completed_bar(_bar(index))
    assert observer.stop(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.accepted == metrics.completed + metrics.failed
    assert metrics.outstanding == 0
    assert metrics.failed == 0
    assert metrics.discovery_cycles == 20
    assert metrics.discovery_detector_evaluations == 20 * 30
    census = ExperienceStore(path).discovery_census()
    assert census["discovery_opportunity_observations"] <= metrics.discovery_raw_firings
    assert census["strategy_membership_observations"] == metrics.discovery_strategy_memberships
    assert census["position_correlation_observations"] >= 1
    assert census["position_thesis_observations"] >= 1
    assert census["strategy_transition_observations"] == metrics.discovery_strategy_transitions
    assert census["strategy_transition_observations"] >= 1


def test_detector_failure_is_counted_and_cannot_escape_market_observer(tmp_path: Path):
    service = TradeIntelligenceService(tmp_path / "failure.sqlite3", capacity=8)

    class BrokenEngine:
        def observe(self, context):
            raise RuntimeError("injected detector failure")

    service._discovery_worker.engine = BrokenEngine()
    observer = TradeIntelligenceRuntimeObserver(
        enabled=True, environment="TEST",
        service_factory=lambda _path, *, capacity: service,
    )
    observer.start()
    observer.observe_completed_bar(_bar(0))
    assert observer.stop(timeout_seconds=10)
    metrics = service.metrics()
    assert metrics.failed == 1
    assert metrics.completed == 1  # The independent outcome BAR still completes.
    assert metrics.outstanding == 0


def test_runtime_discovery_has_no_execution_authority_or_dependencies():
    paths = (
        Path(__file__).parents[2] / "app/trade_intelligence/discovery_runtime.py",
        Path(__file__).parents[2] / "app/trade_intelligence/discovery_worker.py",
    )
    forbidden_calls = {
        "place_order", "submit_order", "authorize_order", "cancel_order",
        "replace_order", "resize_position", "close_position", "submit_exit",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        imports = {
            alias.name for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not calls & forbidden_calls
        assert not any(
            token in name
            for name in imports
            for token in ("broker", "order_placement", "account_mutation", "execution_gateway")
        )

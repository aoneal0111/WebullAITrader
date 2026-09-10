from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.strategies.warrior_momentum.execution_pursuit import (
    ExecutionPursuitDecision,
    assess_top_of_book_pursuit,
    depth_features,
)
from app.strategies.warrior_momentum.order_flow import (
    CapitalFlowSnapshot,
    OrderFlowClassification,
    OrderFlowMemory,
    OrderFlowSnapshot,
    detect_order_flow_capabilities,
    normalize_capital_flow_response,
    normalize_footprint_response,
)
from app.market_data.models import BookLevel


NOW = datetime(2026, 9, 10, 19, 45, tzinfo=timezone.utc)
D = Decimal


def snapshot(delta: str, offset: int = 0) -> OrderFlowSnapshot:
    return OrderFlowSnapshot(
        observed_at=NOW + timedelta(seconds=offset), symbol="DBGI",
        buy_volume=D("100") if D(delta) > 0 else D("20"),
        sell_volume=D("20") if D(delta) > 0 else D("100"),
        delta=D(delta),
    )


def test_footprint_response_normalizes_supported_values_without_fabricating_fields():
    result = normalize_footprint_response(
        {"data": [{"time": NOW, "price": "7.24", "buy_volume": "100", "sell_volume": "40"}]},
        symbol="dbgi", observed_at=NOW,
    )
    assert result[0].symbol == "DBGI"
    assert result[0].delta == D("60")
    assert result[0].total_volume is None


def test_capital_flow_is_normalized_separately_from_footprint_delta():
    result = normalize_capital_flow_response(
        [{"net_inflow": "1200", "largeInflow": "2000", "largeOutflow": "800"}],
        symbol="dbgi", observed_at=NOW,
    )
    assert isinstance(result[0], CapitalFlowSnapshot)
    assert result[0].net_inflow == D("1200")
    assert result[0].large_inflow == D("2000")
    assert result[0].large_outflow == D("800")


def test_capability_detection_is_local_and_does_not_call_provider():
    class Namespace:
        get_footprint = lambda self, *args: None
        get_capital_flow = lambda self, *args: None

    result = detect_order_flow_capabilities(
        type("Client", (), {"market_data": Namespace(), "fundamentals": Namespace()})()
    )
    assert result.footprint and result.capital_flow and not result.streaming


def test_order_flow_memory_is_bounded_and_assesses_persistent_accumulation():
    memory = OrderFlowMemory(maximum_snapshots=2)
    memory.add(snapshot("10"))
    memory.add(snapshot("12", 1))
    memory.add(snapshot("14", 2))
    assert len(memory.snapshots) == 2
    assessment = memory.assess(evaluated_at=NOW + timedelta(seconds=2), maximum_age_seconds=D("5"))
    assert assessment.classification is OrderFlowClassification.STRONG_ACCUMULATION
    assert assessment.supports_pursuit


def test_stale_flow_is_unavailable_not_reused_as_bullish_evidence():
    memory = OrderFlowMemory()
    memory.add(snapshot("10"))
    assessment = memory.assess(evaluated_at=NOW + timedelta(seconds=6), maximum_age_seconds=D("5"))
    assert assessment.classification is OrderFlowClassification.UNAVAILABLE
    assert not assessment.fresh


def _assessment(flow=None):
    depth = depth_features(
        (BookLevel(D("7.22"), D("1000")),),
        (BookLevel(D("7.23"), D("100")),),
    )
    return assess_top_of_book_pursuit(
        evaluated_at=NOW, working_limit=D("7.20"), best_bid=D("7.22"),
        best_ask=D("7.23"), bid_size=D("1000"), ask_size=D("100"),
        spread_percent=D(".14"), maximum_spread_percent=D("1.25"),
        quote_fresh=True, liquidity_ok=True, thesis_valid=True, quality_ok=True,
        replacement_budget_available=True, structural_stop=D("6.93"),
        expected_reward=D(".90"), depth=depth, ask_state="DEPTH_DEPLETION",
        bid_advancing=True, flow=flow,
    )


def test_supportive_flow_can_strengthen_already_qualified_depth_pursuit():
    memory = OrderFlowMemory()
    memory.add(snapshot("10"))
    flow = memory.assess(evaluated_at=NOW, maximum_age_seconds=D("5"))
    result = _assessment(flow)
    assert result.decision is ExecutionPursuitDecision.PURSUE_ONE_LEVEL
    assert result.flow_classification == OrderFlowClassification.SUPPORTIVE


def test_distribution_blocks_aggressive_pursuit_but_does_not_create_strategy_state():
    memory = OrderFlowMemory()
    memory.add(snapshot("-80"))
    flow = memory.assess(evaluated_at=NOW, maximum_age_seconds=D("5"))
    result = _assessment(flow)
    assert result.decision is ExecutionPursuitDecision.BLOCKED
    assert result.reason == "ORDER_FLOW_DISTRIBUTION"


def test_stale_flow_falls_back_to_existing_depth_pursuit():
    memory = OrderFlowMemory()
    memory.add(snapshot("10"))
    flow = memory.assess(evaluated_at=NOW + timedelta(seconds=6), maximum_age_seconds=D("5"))
    assert _assessment(flow).decision is ExecutionPursuitDecision.PURSUE_ONE_LEVEL

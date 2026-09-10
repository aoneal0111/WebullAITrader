from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.market_data.models import BookLevel, MarketEventType, QuotePayload
from app.market_data.validation import validate_event
from app.strategies.warrior_momentum.execution_pursuit import (
    ExecutionPursuitDecision,
    assess_top_of_book_pursuit,
    depth_features,
    depth_transition,
)
from app.webull.market_event_parser import WebullMarketEventParser


NOW = datetime(2026, 9, 10, 19, 45, tzinfo=timezone.utc)
D = Decimal


def levels(start: str, count: int, step: str) -> tuple[BookLevel, ...]:
    return tuple(BookLevel(D(start) + D(step) * index, D(100 - index * 5)) for index in range(count))


def test_sdk_quote_retains_at_most_ten_ordered_levels_and_top_of_book():
    bid_levels = [SimpleNamespace(price=D("7.20") - D(".01") * i, size=100 + i) for i in range(12)]
    ask_levels = [SimpleNamespace(price=D("7.21") + D(".01") * i, size=80 + i) for i in range(12)]
    sdk_quote = SimpleNamespace(
        basic=SimpleNamespace(symbol="DBGI", timestamp=1786030200000),
        bids=bid_levels, asks=ask_levels,
    )
    event = WebullMarketEventParser(clock=lambda: NOW)(("quote", sdk_quote))
    assert event.event_type is MarketEventType.QUOTE
    assert isinstance(event.payload, QuotePayload)
    assert len(event.payload.bids) == len(event.payload.asks) == 10
    assert event.payload.bid == D("7.20")
    assert event.payload.ask == D("7.21")
    validate_event(event)


def test_malformed_deeper_level_falls_back_to_valid_top_of_book():
    event = WebullMarketEventParser(clock=lambda: NOW)({
        "event_type": "QUOTE", "symbol": "XYZ", "timestamp": NOW.isoformat(),
        "bid": "10", "ask": "10.01", "bid_size": "100", "ask_size": "90",
        "bids": ({"price": "10", "size": "100"}, {"price": "bad", "size": "50"}),
    })
    assert isinstance(event.payload, QuotePayload)
    assert event.payload.bid == D("10")
    assert event.payload.bids == (BookLevel(D("10"), D("100")),)
    assert event.payload.asks == (BookLevel(D("10.01"), D("90")),)


def test_depth_features_calculate_near_touch_imbalance_and_microprice():
    bids = levels("10.00", 5, "-.01")
    asks = levels("10.10", 5, ".01")
    result = depth_features(bids, asks, near_levels=3)
    assert result is not None
    assert result.bid_depth == D("450")
    assert result.ask_depth == D("450")
    assert result.near_touch_bid_depth == D("285")
    assert result.near_touch_ask_depth == D("285")
    assert result.imbalance == D("0")
    assert result.microprice == D("10.05")


def test_depth_transition_distinguishes_depletion_replenishment_and_bid_step():
    previous_bids = (BookLevel(D("7.20"), D("100")),)
    previous_asks = (BookLevel(D("7.21"), D("500")),)
    depleted = depth_transition(
        previous_bids, previous_asks,
        (BookLevel(D("7.21"), D("120")),),
        (BookLevel(D("7.21"), D("100")),),
    )
    replenished = depth_transition(
        previous_bids, previous_asks,
        (BookLevel(D("7.20"), D("120")),),
        (BookLevel(D("7.21"), D("900")),),
    )
    assert depleted == ("DEPTH_DEPLETION", True)
    assert replenished == ("DEPTH_REPLENISHMENT", False)


def _assessment(**overrides):
    values = dict(
        evaluated_at=NOW, working_limit=D("7.20"), best_bid=D("7.22"),
        best_ask=D("7.23"), bid_size=D("120"), ask_size=D("100"),
        spread_percent=D(".14"), maximum_spread_percent=D("1.25"),
        quote_fresh=True, liquidity_ok=True, thesis_valid=True, quality_ok=True,
        replacement_budget_available=True, structural_stop=D("6.93"),
        expected_reward=D(".90"),
    )
    values.update(overrides)
    return assess_top_of_book_pursuit(**values)


def test_depth_pressure_requires_correlated_book_change_not_one_large_bid():
    depth = depth_features(
        (BookLevel(D("7.22"), D("1000")), BookLevel(D("7.21"), D("1000"))),
        (BookLevel(D("7.23"), D("100")), BookLevel(D("7.24"), D("100"))),
    )
    assert depth is not None
    result = _assessment(depth=depth, ask_state="UNCHANGED", bid_advancing=False)
    assert result.decision is ExecutionPursuitDecision.HOLD_PASSIVE


def test_depth_depletion_and_bid_step_can_strengthen_one_level_pursuit():
    depth = depth_features(
        (BookLevel(D("7.22"), D("1000")), BookLevel(D("7.21"), D("1000"))),
        (BookLevel(D("7.23"), D("100")), BookLevel(D("7.24"), D("100"))),
    )
    result = _assessment(depth=depth, ask_state="DEPTH_DEPLETION", bid_advancing=True)
    assert result.decision is ExecutionPursuitDecision.PURSUE_ONE_LEVEL
    assert result.proposed_limit == D("7.23")


def test_depth_never_overrides_spread_or_exhausted_quality():
    depth = depth_features(
        (BookLevel(D("7.22"), D("1000")),),
        (BookLevel(D("7.23"), D("100")),),
    )
    assert _assessment(depth=depth, ask_state="DEPTH_DEPLETION", bid_advancing=True,
                       spread_percent=D("1.26")).reason == "SPREAD_WIDE"
    assert _assessment(depth=depth, ask_state="DEPTH_DEPLETION", bid_advancing=True,
                       quality_ok=False).reason == "OPPORTUNITY_QUALITY_BLOCKED"

from dataclasses import dataclass, replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.composition.runtime_mode import RuntimeMode
from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.order_cancellation import OrderCancellationRequest
from app.paper_trading.order_models import OrderType
from app.performance_diagnostics import performance_diagnostics
from tests.test_support.session_clock import (
    create_session_paper_composition as create_paper_trading_command_composition,
    session_timestamp,
)
from app.services.order_command_factory import OrderEntryCommand
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge
from app.strategies.warrior_momentum.autonomous_paper import (
    AutonomousManagementReadiness, AutonomousPaperReadiness,
    PaperExitSubmissionDecision, PaperExitSubmissionFailureReason,
    PaperExitSubmissionState,
    PaperEntryReplacementPolicy, PaperEntryReplacementState,
)


@dataclass(frozen=True)
class Signal:
    symbol: str = "PMI"
    entry_trigger: Decimal = Decimal("10")
    stop_price: Decimal = Decimal("9.50")
    lifecycle_id: str = "trade-a"


def test_paper_bridge_submits_exactly_one_entry_and_exit_per_transition() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        mode=RuntimeMode.PAPER.value, order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is True
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is False
    assert len(composition.order_book.open_orders()) == 1
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "TARGET") is True
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "TARGET") is False
    assert len(composition.order_book.open_orders()) == 2
    composition.close()


def test_add_on_is_one_correlated_limit_buy_and_is_idempotent() -> None:
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is True
    composition.gateway.process_market_event(MarketEvent(
        1, session_timestamp(1), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal("9.99"), Decimal("10"), Decimal("100"), Decimal("100")),
    ))
    first = bridge.submit_add_on(
        Signal(entry_trigger=Decimal("10.10")), 25, Decimal("2.50"),
        parent_lifecycle_id="trade-a", add_on_id="trade-a:ADD_ON_1",
    )
    assert first.authorized is True
    add_on = composition.order_book.history()[-1]
    assert add_on.request.order_type is OrderType.LIMIT
    assert add_on.request.metadata["provenance"] == "AUTONOMOUS_ADD_ON_ENTRY"
    assert bridge.submit_add_on(
        Signal(entry_trigger=Decimal("10.10")), 25, Decimal("2.50"),
        parent_lifecycle_id="trade-a", add_on_id="trade-a:ADD_ON_1",
    ).authorized is False
    composition.close()


def test_bridge_refuses_non_paper_mode_without_invoking_order_port() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        mode="LIVE", order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is False
    assert composition.order_book.history() == ()
    composition.close()


def test_paper_bridge_round_trip_uses_gateway_fill_lifecycle() -> None:
    events = []
    composition = create_paper_trading_command_composition(
        event_sink=events.append,
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is True
    entry_reports = composition.gateway.process_market_event(MarketEvent(
        1, session_timestamp(1), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal("9.99"), Decimal("10"), Decimal("100"), Decimal("100")),
    ))
    assert entry_reports and entry_reports[0].fills
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "TARGET") is True
    exit_reports = composition.gateway.process_market_event(MarketEvent(
        2, session_timestamp(2), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal("10.50"), Decimal("10.51"), Decimal("100"), Decimal("100")),
    ))
    assert exit_reports and exit_reports[0].fills
    assert events[-1].fill is not None
    assert events[-1].fill.realized_pnl == Decimal("50.00")
    composition.close()


def test_same_symbol_sequential_lifecycles_allow_reentry_and_same_exit_reason() -> None:
    quantity = {"PMI": Decimal("100")}
    events = []
    composition = create_paper_trading_command_composition(
        event_sink=events.append,
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda symbol: quantity.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )

    def quote(sequence: int, bid: str, ask: str) -> None:
        composition.gateway.process_market_event(MarketEvent(
            sequence, session_timestamp(sequence), "PMI", "test", MarketEventType.QUOTE,
            QuotePayload(Decimal(bid), Decimal(ask), Decimal("100"), Decimal("100")),
        ))

    trade_a = Signal(lifecycle_id="trade-a")
    assert bridge.submit_entry(trade_a, 100, Decimal("50")) is True
    assert bridge.submit_entry(trade_a, 100, Decimal("50")) is False
    quote(1, "9.99", "10")
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "STOP", "trade-a") is True
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "STOP", "trade-a") is False
    quote(2, "10.50", "10.51")
    quantity["PMI"] = Decimal("0")

    trade_b = Signal(entry_trigger=Decimal("11"), lifecycle_id="trade-b")
    assert bridge.submit_entry(trade_b, 100, Decimal("50")) is True
    assert bridge.submit_entry(trade_b, 100, Decimal("50")) is False
    quote(3, "10.99", "11")
    quantity["PMI"] = Decimal("100")
    assert bridge.submit_exit("PMI", 100, Decimal("9.75"), "STOP", "trade-b") is True
    assert bridge.submit_exit("PMI", 100, Decimal("9.75"), "STOP", "trade-b") is False
    quote(4, "9.75", "9.76")

    assert len(composition.order_book.history()) == 4
    fills = [event.fill for event in events if event.fill is not None]
    assert len(fills) == 4
    assert sum((fill.realized_pnl for fill in fills), Decimal("0")) == Decimal("25.00")
    assert bridge.submit_entry(trade_a, 100, Decimal("50")) is False
    assert bridge.submit_exit("PMI", 100, Decimal("9.75"), "STOP", "trade-a") is False
    composition.close()


def _paper_quote(composition, sequence: int, bid: str, ask: str) -> None:
    composition.gateway.process_market_event(MarketEvent(
        sequence, session_timestamp(sequence), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal(bid), Decimal(ask), Decimal("100"), Decimal("100")),
    ))


def test_restart_working_entry_reconciles_and_later_fills_once(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(persistence_path=str(path))
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    first.close()
    second = create_paper_trading_command_composition(persistence_path=str(path))
    recovered = AutonomousPaperExecutionBridge(second.trading_service, second.order_command_factory, order_book=second.order_book)
    recovered.begin_reconciliation()
    assert recovered.submit_entry(Signal(), 100, Decimal("50")) is False
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    _paper_quote(second, 10, "9.99", "10")
    assert len(second.order_book.history()) == 1
    assert second.order_book.history()[0].filled_quantity == Decimal("100")
    assert recovered.management_readiness("PMI") is AutonomousManagementReadiness.RECOVERED_READY
    second.close()


def test_restored_entry_below_structural_stop_cancels_before_fill(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(persistence_path=str(path))
    bridge = AutonomousPaperExecutionBridge(
        first.trading_service, first.order_command_factory,
        order_book=first.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    order_id = first.order_book.open_orders()[0].order_id
    first.close()

    events = []
    second = create_paper_trading_command_composition(
        persistence_path=str(path), event_sink=events.append,
    )
    recovered = AutonomousPaperExecutionBridge(
        second.trading_service, second.order_command_factory,
        order_book=second.order_book,
    )
    recovered.begin_reconciliation()
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    _paper_quote(second, 2, "9.39", "9.40")

    order = second.order_book.get(order_id)
    assert order.status.value == "CANCELLED"
    assert order.filled_quantity == Decimal("0")
    assert order.remaining_quantity == Decimal("100")
    assert [event.event_type for event in events][-1] == "ORDER_CANCELLED"
    assert recovered.has_execution_ownership("PMI") is False
    assert recovered.submit_entry(Signal(), 100, Decimal("50")) is False
    assert recovered.submit_entry(
        Signal(lifecycle_id="trade-b"), 100, Decimal("50"),
    ) is True
    second.close()


def test_explicit_entry_replacement_cancels_then_submits_correlated_limit() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 1000, Decimal("1000"))
    predecessor = composition.order_book.open_orders()[0]

    decision = bridge.replace_entry_order(
        lifecycle_id="trade-a",
        replacement_limit=Decimal("10.50"),
        structural_stop=Decimal("9.50"),
        original_risk_budget=Decimal("1000"),
        account_equity=Decimal("10000"),
        buying_power=Decimal("10000"),
        revalidate=lambda: True,
        reason="fast-entry-foundation",
    )

    assert decision.state is PaperEntryReplacementState.SUBMITTED
    assert decision.replacement_sequence == 1
    assert decision.predecessor_order_id == predecessor.order_id
    assert decision.requested_quantity == 476  # 5,000 / 10.50, risk binds first.
    assert composition.order_book.get(predecessor.order_id).status.value == "CANCELLED"
    replacement = composition.order_book.get(decision.replacement_order_id)
    assert replacement.request.order_type.value == "LIMIT"
    assert replacement.request.metadata["predecessor_order_id"] == predecessor.order_id
    assert replacement.request.metadata["provenance"] == "AUTONOMOUS_ADAPTIVE_ENTRY_REPLACEMENT"
    assert replacement.request.metadata["replacement_sequence"] == "1"
    assert all(order.request.order_type.value != "MARKET" for order in composition.order_book.history())
    composition.close()


def test_entry_replacement_requires_confirmed_cancel_and_fresh_revalidation() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    predecessor = composition.order_book.open_orders()[0]
    with patch.object(AutonomousPaperExecutionBridge, "_cancel_entry_predecessor", return_value=False):
        refused = bridge.replace_entry_order(
            lifecycle_id="trade-a", replacement_limit=Decimal("10.50"),
            structural_stop=Decimal("9.50"), original_risk_budget=Decimal("50"),
            account_equity=Decimal("10000"), buying_power=Decimal("10000"),
            revalidate=lambda: True,
        )
    assert refused.reason == "CANCELLATION_NOT_CONFIRMED"
    assert composition.order_book.get(predecessor.order_id).is_terminal is False
    assert len(composition.order_book.open_orders()) == 1
    refused = bridge.replace_entry_order(
        lifecycle_id="trade-a", replacement_limit=Decimal("10.50"),
        structural_stop=Decimal("9.50"), original_risk_budget=Decimal("50"),
        account_equity=Decimal("10000"), buying_power=Decimal("10000"),
        revalidate=lambda: False,
    )
    assert refused.reason == "REVALIDATION_FAILED"
    assert len(composition.order_book.history()) == 1
    composition.close()


def test_partial_fill_replacement_uses_remaining_risk_and_quantity() -> None:
    quantity = {"PMI": Decimal("400")}
    now = [session_timestamp(0)]
    clock = lambda: now[0]
    composition = create_paper_trading_command_composition(
        clock=clock,
        position_quantity_source=lambda symbol: quantity.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: quantity.get(symbol, Decimal("0")),
    )
    assert bridge.submit_entry(Signal(), 1000, Decimal("1000"))
    for sequence in range(1, 5):
        _paper_quote(composition, sequence, "9.99", "10")
    now[0] = session_timestamp(10)
    predecessor = composition.order_book.open_orders()[0]
    assert predecessor.filled_quantity == Decimal("400")
    decision = bridge.replace_entry_order(
        lifecycle_id="trade-a", replacement_limit=Decimal("11.50"),
        structural_stop=Decimal("9.50"), original_risk_budget=Decimal("1000"),
        account_equity=Decimal("10000"), buying_power=Decimal("10000"),
        revalidate=lambda: True,
    )
    assert decision.submitted
    # 400 filled consumes 200 risk; 800 remains / 2.00 risk per share.
    assert decision.requested_quantity == 400
    assert decision.cumulative_filled_quantity == Decimal("400")
    replacement = composition.order_book.get(decision.replacement_order_id)
    assert replacement.request.quantity == Decimal("400")
    composition.close()


def test_replacement_refuses_when_risk_or_notional_is_exhausted() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    exhausted = bridge.replace_entry_order(
        lifecycle_id="trade-a", replacement_limit=Decimal("10.50"),
        structural_stop=Decimal("9.50"), original_risk_budget=Decimal("0"),
        account_equity=Decimal("10000"), buying_power=Decimal("10000"),
        revalidate=lambda: True,
    )
    assert exhausted.reason == "REMAINING_AUTHORIZATION_EXHAUSTED"
    assert len(composition.order_book.history()) == 1
    composition.close()


def test_replacement_respects_notional_and_buying_power_caps() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 1000, Decimal("1000"))
    capped = bridge.replace_entry_order(
        lifecycle_id="trade-a", replacement_limit=Decimal("10.50"),
        structural_stop=Decimal("9.50"), original_risk_budget=Decimal("1000"),
        account_equity=Decimal("10000"), buying_power=Decimal("10000"),
        maximum_position_equity_percentage=Decimal("0.10"),
        revalidate=lambda: True,
    )
    assert capped.submitted
    assert capped.requested_quantity == 95  # $1,000 / $10.50, floored.
    composition.close()

    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    refused = bridge.replace_entry_order(
        lifecycle_id="trade-a", replacement_limit=Decimal("10.50"),
        structural_stop=Decimal("9.50"), original_risk_budget=Decimal("50"),
        account_equity=Decimal("10000"), buying_power=Decimal("1"),
        revalidate=lambda: True,
    )
    assert refused.reason == "REMAINING_AUTHORIZATION_EXHAUSTED"
    assert len(composition.order_book.open_orders()) == 0
    composition.close()


def test_replacement_metadata_survives_restart_without_duplicate_entry(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(persistence_path=str(path))
    bridge = AutonomousPaperExecutionBridge(
        first.trading_service, first.order_command_factory, order_book=first.order_book,
    )
    assert bridge.submit_entry(Signal(), 1000, Decimal("1000"))
    decision = bridge.replace_entry_order(
        lifecycle_id="trade-a", replacement_limit=Decimal("10.50"),
        structural_stop=Decimal("9.50"), original_risk_budget=Decimal("1000"),
        account_equity=Decimal("10000"), buying_power=Decimal("10000"),
        revalidate=lambda: True,
    )
    first.close()
    second = create_paper_trading_command_composition(persistence_path=str(path))
    recovered = AutonomousPaperExecutionBridge(
        second.trading_service, second.order_command_factory, order_book=second.order_book,
    )
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert len(second.order_book.history()) == 2
    replacement = second.order_book.get(decision.replacement_order_id)
    assert replacement.request.metadata["predecessor_order_id"] == decision.predecessor_order_id
    assert recovered.submit_entry(Signal(), 1000, Decimal("1000")) is False
    second.close()


def test_bounded_chase_uses_both_displacement_caps_and_stops_after_two_replacements() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(entry_trigger=Decimal("1.25")), 100, Decimal("50"))
    policy = PaperEntryReplacementPolicy(min_reprice_interval_seconds=Decimal("0"))
    common = dict(
        lifecycle_id="trade-a", structural_stop=Decimal("1.20"),
        original_risk_budget=Decimal("50"), account_equity=Decimal("10000"),
        buying_power=Decimal("10000"), revalidate=lambda: True,
        policy=policy, now=session_timestamp(1),
    )
    first = bridge.consider_entry_replacement(current_ask=Decimal("1.26"), **common)
    assert first.submitted
    assert first.replacement_sequence == 1
    second = bridge.consider_entry_replacement(
        current_ask=Decimal("1.268"), now=session_timestamp(2), **{k: v for k, v in common.items() if k != "now"},
    )
    assert second.submitted
    assert second.replacement_sequence == 2
    exhausted = bridge.consider_entry_replacement(
        current_ask=Decimal("1.2685"), now=session_timestamp(3), **{k: v for k, v in common.items() if k != "now"},
    )
    assert exhausted.reason == "REPLACEMENT_LIMIT_EXHAUSTED"
    over_chase = bridge.consider_entry_replacement(
        current_ask=Decimal("1.275"), now=session_timestamp(4), **{k: v for k, v in common.items() if k != "now"},
    )
    assert over_chase.reason == "REPLACEMENT_LIMIT_EXHAUSTED"
    assert all(order.request.order_type.value == "LIMIT" for order in composition.order_book.history())
    composition.close()


def test_bounded_chase_preserves_original_deadline_and_rejects_unrevalidated_state() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(entry_trigger=Decimal("10")), 100, Decimal("50"))
    refused = bridge.consider_entry_replacement(
        lifecycle_id="trade-a", current_ask=Decimal("10.02"),
        now=session_timestamp(61), structural_stop=Decimal("9.50"),
        original_risk_budget=Decimal("50"), account_equity=Decimal("10000"),
        buying_power=Decimal("10000"), revalidate=lambda: True,
    )
    assert refused.reason == "CHASE_WINDOW_EXPIRED"
    assert len(composition.order_book.open_orders()) == 1
    refused = bridge.consider_entry_replacement(
        lifecycle_id="trade-a", current_ask=Decimal("10.02"),
        now=session_timestamp(1), structural_stop=Decimal("9.50"),
        original_risk_budget=Decimal("50"), account_equity=Decimal("10000"),
        buying_power=Decimal("10000"), revalidate=lambda: False,
        policy=PaperEntryReplacementPolicy(min_reprice_interval_seconds=Decimal("0")),
    )
    assert refused.reason == "REVALIDATION_FAILED"
    assert len(composition.order_book.open_orders()) == 0
    assert all(order.request.order_type.value != "MARKET" for order in composition.order_book.history())
    composition.close()


def test_bounded_chase_rejects_price_beyond_effective_cap_before_count_is_used() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(entry_trigger=Decimal("10")), 100, Decimal("50"))
    refused = bridge.consider_entry_replacement(
        lifecycle_id="trade-a", current_ask=Decimal("10.06"),
        now=session_timestamp(1), structural_stop=Decimal("9.50"),
        original_risk_budget=Decimal("50"), account_equity=Decimal("10000"),
        buying_power=Decimal("10000"), revalidate=lambda: True,
        policy=PaperEntryReplacementPolicy(min_reprice_interval_seconds=Decimal("0")),
    )
    assert refused.reason == "CHASE_LIMIT_EXCEEDED"  # 1.5%/$0.05 cap => $10.05.
    assert len(composition.order_book.open_orders()) == 1
    composition.close()


def test_reprice_cadence_throttles_orders_but_not_observation_evaluation() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(entry_trigger=Decimal("1.25")), 100, Decimal("50"))
    common = dict(
        lifecycle_id="trade-a", structural_stop=Decimal("1.20"),
        original_risk_budget=Decimal("50"), account_equity=Decimal("10000"),
        buying_power=Decimal("10000"), revalidate=lambda: True,
    )
    early = bridge.consider_entry_replacement(
        current_ask=Decimal("1.255"), now=session_timestamp(4.9),
        **common,
    )
    # The fixed composition clock is the original order timestamp; use an
    # explicit policy interval to make the boundary deterministic below.
    assert early.reason == "REPRICE_INTERVAL_NOT_ELAPSED"
    policy = PaperEntryReplacementPolicy(min_reprice_interval_seconds=Decimal("5"))
    first = bridge.consider_entry_replacement(
        current_ask=Decimal("1.255"), now=session_timestamp(5), policy=policy, **common,
    )
    assert first.submitted
    too_soon = bridge.consider_entry_replacement(
        current_ask=Decimal("1.260"), now=session_timestamp(9), policy=policy, **common,
    )
    assert too_soon.reason == "REPRICE_INTERVAL_NOT_ELAPSED"
    second = bridge.consider_entry_replacement(
        current_ask=Decimal("1.260"), now=session_timestamp(10), policy=policy, **common,
    )
    assert second.submitted
    composition.close()


def test_rearmed_lifecycles_share_opportunity_anchor_and_are_capped() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    first = Signal(entry_trigger=Decimal("1.25"), lifecycle_id="trade-a")
    assert bridge.submit_entry_decision(
        first, 100, Decimal("50"), opportunity_id="opp-a",
    ).authorized
    first_order = composition.order_book.open_orders_for_symbol("PMI")[0]
    assert bridge._cancel_entry_predecessor(first_order)
    second = Signal(entry_trigger=Decimal("1.26"), lifecycle_id="trade-b")
    decision = bridge.submit_rearmed_entry(
        second, 100, Decimal("50"), opportunity_id="opp-a",
    )
    assert decision.authorized
    second_order = composition.order_book.open_orders_for_symbol("PMI")[0]
    assert second_order.request.metadata["opportunity_id"] == "opp-a"
    assert second_order.request.metadata["opportunity_original_entry"] == "1.25"
    assert second_order.request.order_type is OrderType.LIMIT
    assert bridge._cancel_entry_predecessor(second_order)
    third = Signal(entry_trigger=Decimal("1.268"), lifecycle_id="trade-c")
    assert bridge.submit_rearmed_entry(
        third, 100, Decimal("50"), opportunity_id="opp-a",
    ).authorized
    third_order = composition.order_book.open_orders_for_symbol("PMI")[0]
    assert third_order.request.metadata["opportunity_original_entry"] == "1.25"
    assert bridge._cancel_entry_predecessor(third_order)
    fourth = Signal(entry_trigger=Decimal("1.2685"), lifecycle_id="trade-d")
    refused = bridge.submit_rearmed_entry(
        fourth, 100, Decimal("50"), opportunity_id="opp-a",
    )
    assert refused.reason.name == "OPPORTUNITY_LIFECYCLE_LIMIT_EXHAUSTED"
    assert all(
        order.request.order_type is OrderType.LIMIT
        for order in composition.order_book.history()
    )
    composition.close()


def test_partial_entry_invalidation_preserves_fill_and_cancels_remainder() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    order_id = composition.order_book.open_orders()[0].order_id
    composition.gateway.process_market_event(MarketEvent(
        1, session_timestamp(1), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal("9.99"), Decimal("10"), Decimal("40"), Decimal("40")),
    ))
    composition.gateway.process_market_event(MarketEvent(
        2, session_timestamp(2), "PMI", "test", MarketEventType.QUOTE,
        QuotePayload(Decimal("9.39"), Decimal("9.40"), Decimal("100"), Decimal("100")),
    ))

    order = composition.order_book.get(order_id)
    assert order.status.value == "CANCELLED"
    assert order.filled_quantity == Decimal("40")
    assert order.remaining_quantity == Decimal("60")
    assert sum(fill.quantity for fill in order.fills) == Decimal("40")
    protection = bridge.ensure_exit(
        "PMI", 40, Decimal("9.50"), "STOP", "trade-a",
    )
    assert protection.protection_active
    composition.close()


def test_stop_exit_is_protective_and_gap_fills_at_bid() -> None:
    composition = create_paper_trading_command_composition(
        position_average_cost_source=lambda _symbol: Decimal("10"),
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(composition, 1, "9.99", "10")
    decision = bridge.ensure_exit("PMI", 100, Decimal("5.03"), "STOP", "trade-a")
    assert decision.protection_active
    sell = next(order for order in composition.order_book.open_orders()
                if order.request.side.value == "SELL")
    assert sell.request.order_type.value == "STOP"
    assert sell.request.limit_price is None
    assert sell.request.stop_price == Decimal("5.03")

    _paper_quote(composition, 2, "4.50", "4.51")
    sell = composition.order_book.get(sell.order_id)
    assert sell.status.value == "FILLED"
    assert sell.average_fill_price == Decimal("4.50")
    composition.close()


def test_duplicate_stop_signal_keeps_one_working_protective_order() -> None:
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(composition, 1, "9.99", "10")
    first = bridge.ensure_exit("PMI", 100, Decimal("9.50"), "STOP", "trade-a")
    second = bridge.ensure_exit("PMI", 100, Decimal("9.50"), "STOP", "trade-a")

    assert first.state.value == "SUBMITTED"
    assert second.state.value == "WORKING"
    assert first.order_id == second.order_id
    assert len([order for order in composition.order_book.history()
                if order.request.side.value == "SELL"]) == 1
    composition.close()


def test_session_cutoff_cancels_buy_entries_but_preserves_protective_sells() -> None:
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: Decimal("100") if symbol == "PMI" else Decimal("0"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(composition, 1, "9.99", "10")
    assert bridge.ensure_exit(
        "PMI", 100, Decimal("9.50"), "STOP", "trade-a",
    ).protection_active
    assert bridge.submit_entry(
        Signal(symbol="LATE", lifecycle_id="trade-late"), 10, Decimal("5"),
    )

    cancelled = bridge.cancel_working_entries()

    assert len(cancelled) == 1
    remaining = composition.order_book.open_orders()
    assert len(remaining) == 1
    assert remaining[0].request.side.value == "SELL"
    assert remaining[0].request.order_type is OrderType.STOP
    composition.close()


def test_session_close_replaces_protection_with_market_exit() -> None:
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(composition, 1, "9.99", "10")
    assert bridge.ensure_exit(
        "PMI", 100, Decimal("9.50"), "STOP", "trade-a",
    ).protection_active
    _paper_quote(composition, 2, "10.20", "10.21")

    decision = bridge.ensure_exit(
        "PMI", 100, Decimal("10.25"), "SESSION_CLOSE", "trade-a",
    )

    assert decision.state.value == "SUBMITTED"
    working = composition.order_book.open_orders()
    assert len(working) == 1
    assert working[0].request.order_type is OrderType.MARKET
    assert working[0].request.time_in_force.value == "GTC"
    composition.close()


def test_target_coordinates_with_protection_and_authoritative_partial_remainder() -> None:
    position = {"PMI": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    def full_quote(sequence: int, bid: str, ask: str) -> None:
        composition.gateway.process_market_event(MarketEvent(
            sequence, session_timestamp(sequence), "PMI", "test", MarketEventType.QUOTE,
            QuotePayload(Decimal(bid), Decimal(ask), Decimal("759"), Decimal("759")),
        ))
    try:
        assert bridge.submit_entry(Signal(), 759, Decimal("50"))
        full_quote(1, "9.99", "10")
        position["PMI"] = Decimal("759")
        protection = bridge.ensure_exit("PMI", 759, Decimal("9.50"), "STOP", "trade-a")
        assert protection.protection_active

        first = bridge.ensure_exit("PMI", 379, Decimal("10.50"), "FIRST_TARGET", "trade-a")
        sells = [order for order in composition.order_book.open_orders_for_symbol("PMI")
                 if order.request.side.value == "SELL"]
        assert {order.request.order_type.value for order in sells} == {"LIMIT", "STOP"}
        assert sorted(int(order.quantity) for order in sells) == [379, 759]
        remainder_stop = next(
            order for order in sells if order.request.order_type.value == "STOP"
        )
        assert remainder_stop.request.metadata["reservation_mode"] == "CONTINGENT_OCO"
        assert remainder_stop.request.metadata["correlated_target_order_id"] == first.order_id
        assert remainder_stop.request.stop_price == Decimal("10")

        full_quote(2, "10.50", "10.51")
        position["PMI"] = Decimal("380")
        second = bridge.ensure_exit("PMI", 189, Decimal("11.00"), "SECOND_TARGET", "trade-a")
        assert second.protection_active
        sells = [order for order in composition.order_book.open_orders_for_symbol("PMI")
                 if order.request.side.value == "SELL"]
        assert sorted(int(order.quantity) for order in sells) == [189, 380]

        full_quote(3, "11.00", "11.01")
        position["PMI"] = Decimal("191")
        bridge.ensure_exit("PMI", 191, Decimal("9.50"), "STOP", "trade-a")
        stops = [order for order in composition.order_book.open_orders_for_symbol("PMI")
                 if order.request.order_type.value == "STOP"]
        assert len(stops) == 1 and int(stops[0].quantity) == 191
        assert first.order_id != second.order_id
    finally:
        composition.close()


def test_cancelled_protective_exit_retries_for_authoritative_remainder() -> None:
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(composition, 1, "9.99", "10")
    first = bridge.ensure_exit("PMI", 100, Decimal("9.50"), "STOP", "trade-a")
    first_order = composition.order_book.get(first.order_id)
    cancelled = composition.gateway.cancel_order(OrderCancellationRequest(
        request_id="cancel-protection",
        session_id=composition.session_id,
        account_id=composition.account_id,
        broker_order_id=first.order_id,
        client_order_id=first_order.request.client_order_id,
    ))
    assert cancelled is not None and cancelled.accepted

    retried = bridge.ensure_exit("PMI", 100, Decimal("9.50"), "STOP", "trade-a")
    assert retried.state.value == "SUBMITTED"
    assert retried.order_id != first.order_id
    assert len([order for order in composition.order_book.history()
                if order.request.side.value == "SELL"]) == 2
    composition.close()


def test_triggered_stop_cancels_passive_target_before_protective_replace() -> None:
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
    )
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(composition, 1, "9.99", "10")
    target = bridge.ensure_exit(
        "PMI", 100, Decimal("10.50"), "RUNNER_TARGET", "trade-a",
    )
    assert target.protection_active

    stop = bridge.ensure_exit(
        "PMI", 100, Decimal("9.50"), "STOP", "trade-a",
    )
    assert stop.state.value == "SUBMITTED"
    assert composition.order_book.get(target.order_id).status.value == "CANCELLED"
    protective = composition.order_book.get(stop.order_id)
    assert protective.request.order_type.value == "STOP"
    assert protective.request.stop_price == Decimal("9.50")
    assert len(composition.order_book.open_orders_for_symbol("PMI")) == 1
    composition.close()


def test_restart_open_position_blocks_duplicate_entry_and_uses_restored_quantity(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(first, 1, "9.99", "10")
    first.close()
    second = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    recovered = AutonomousPaperExecutionBridge(second.trading_service, second.order_command_factory, order_book=second.order_book, position_quantity_source=lambda _symbol: Decimal("100"))
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert recovered.submit_entry(Signal(), 100, Decimal("50")) is False
    assert recovered.management_readiness("PMI") is AutonomousManagementReadiness.RECOVERED_READY
    assert recovered.submit_exit("PMI", 100, Decimal("10.50"), "STOP") is True
    second.close()


def test_restart_pending_exit_suppresses_duplicate_and_future_fill_closes(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(first, 1, "9.99", "10")
    assert bridge.submit_exit("PMI", 100, Decimal("10.50"), "STOP")
    first.close()
    second = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    recovered = AutonomousPaperExecutionBridge(second.trading_service, second.order_command_factory, order_book=second.order_book, position_quantity_source=lambda _symbol: Decimal("100"))
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert recovered.submit_exit("PMI", 100, Decimal("10.50"), "STOP") is False
    restored_exit = next(order for order in second.order_book.open_orders()
                         if order.request.side.value == "SELL")
    assert restored_exit.request.order_type.value == "STOP"
    assert restored_exit.request.execution_reason == "STOP"
    _paper_quote(second, 2, "10.50", "10.51")
    assert len(second.order_book.history()) == 2
    assert second.order_book.open_orders() == ()
    second.close()


def test_recovered_position_management_uses_durable_lifecycle_without_context(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(first, 1, "9.99", "10")
    first.close()
    second = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    recovered = AutonomousPaperExecutionBridge(second.trading_service, second.order_command_factory, order_book=second.order_book, position_quantity_source=lambda _symbol: Decimal("100"))
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert recovered.management_readiness("PMI") is AutonomousManagementReadiness.RECOVERED_READY
    second.close()


def test_recovered_position_with_verified_context_is_management_ready(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"),
    )
    bridge = AutonomousPaperExecutionBridge(first.trading_service, first.order_command_factory, order_book=first.order_book)
    assert bridge.submit_entry(Signal(), 100, Decimal("50"))
    _paper_quote(first, 1, "9.99", "10")
    first.close()
    second = create_paper_trading_command_composition(persistence_path=str(path), position_quantity_source=lambda _symbol: Decimal("100"))
    recovered = AutonomousPaperExecutionBridge(
        second.trading_service, second.order_command_factory, order_book=second.order_book,
        position_quantity_source=lambda _symbol: Decimal("100"),
        management_context_source=lambda _symbol: "trade-a",
    )
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert recovered.management_readiness("PMI") is AutonomousManagementReadiness.READY
    second.close()


def test_recovered_matching_stop_allows_first_target_without_context(tmp_path) -> None:
    """Durable lifecycle authority restores a target beside an existing stop."""
    path = tmp_path / "paper.sqlite3"
    first = create_paper_trading_command_composition(
        persistence_path=str(path),
        position_quantity_source=lambda _symbol: Decimal("25"),
    )
    bridge = AutonomousPaperExecutionBridge(
        first.trading_service, first.order_command_factory,
        order_book=first.order_book,
        position_quantity_source=lambda _symbol: Decimal("25"),
    )
    assert bridge.submit_entry(Signal(), 25, Decimal("50"))
    _paper_quote(first, 1, "9.99", "10")
    assert bridge.submit_exit("PMI", 25, Decimal("9.50"), "STOP")
    first.close()

    second = create_paper_trading_command_composition(
        persistence_path=str(path),
        position_quantity_source=lambda _symbol: Decimal("25"),
    )
    recovered = AutonomousPaperExecutionBridge(
        second.trading_service, second.order_command_factory,
        order_book=second.order_book,
        position_quantity_source=lambda _symbol: Decimal("25"),
    )
    assert recovered.reconcile() is AutonomousPaperReadiness.READY
    assert recovered.management_readiness("PMI") is AutonomousManagementReadiness.RECOVERED_READY
    target = recovered.ensure_exit("PMI", 23, Decimal("10.50"), "FIRST_TARGET")
    assert target.protection_active is True
    sells = second.order_book.open_orders_for_symbol("PMI")
    assert len(sells) == 2
    assert sum(order.request.execution_reason == "FIRST_TARGET" for order in sells) == 1
    assert sum(order.request.execution_reason == "STOP" for order in sells) == 1
    again = recovered.ensure_exit("PMI", 23, Decimal("10.50"), "FIRST_TARGET")
    assert again.protection_active is True
    assert len(second.order_book.open_orders_for_symbol("PMI")) == 2
    second.close()


def test_reconciliation_barrier_and_contradiction_fail_closed(tmp_path) -> None:
    path = tmp_path / "paper.sqlite3"
    composition = create_paper_trading_command_composition(persistence_path=str(path))
    bridge = AutonomousPaperExecutionBridge(composition.trading_service, composition.order_command_factory, order_book=composition.order_book)
    bridge.begin_reconciliation()
    assert bridge.readiness is AutonomousPaperReadiness.RECONCILING
    assert bridge.submit_entry(Signal(), 100, Decimal("50")) is False
    assert bridge.reconcile() is AutonomousPaperReadiness.READY
    composition.close()

    first = create_paper_trading_command_composition(persistence_path=str(path))
    command = OrderEntryCommand(symbol="PMI", side="BUY", quantity=Decimal("100"), order_type="LIMIT", limit_price=Decimal("10"), stop_price=None, time_in_force="DAY")
    assert first.trading_service.place_order(first.order_command_factory.create_placement_request(command)).success
    command = OrderEntryCommand(symbol="PMI", side="BUY", quantity=Decimal("100"), order_type="LIMIT", limit_price=Decimal("10"), stop_price=None, time_in_force="DAY")
    assert first.trading_service.place_order(first.order_command_factory.create_placement_request(command)).success
    first.close()
    blocked = create_paper_trading_command_composition(persistence_path=str(path))
    blocked_bridge = AutonomousPaperExecutionBridge(blocked.trading_service, blocked.order_command_factory, order_book=blocked.order_book)
    assert blocked_bridge.reconcile() is AutonomousPaperReadiness.BLOCKED
    assert blocked_bridge.submit_entry(Signal(), 100, Decimal("50")) is False
    blocked.close()


def test_recovery_active_lifecycle_conflict_stays_blocked_and_preserves_stop(
    tmp_path,
) -> None:
    path = tmp_path / "active-lifecycle-conflict.sqlite3"
    position = {"PMI": Decimal("0")}
    first = create_paper_trading_command_composition(
        persistence_path=str(path),
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    bridge = AutonomousPaperExecutionBridge(
        first.trading_service,
        first.order_command_factory,
        order_book=first.order_book,
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    assert bridge.submit_entry(Signal(), 25, Decimal("12.50"))
    _paper_quote(first, 1, "9.99", "10")
    position["PMI"] = Decimal("25")
    stop = bridge.ensure_exit(
        "PMI", 25, Decimal("9.50"), "STOP", "trade-a",
    )
    conflict = first.trading_service.place_order(
        first.order_command_factory.create_placement_request(
            OrderEntryCommand(
                symbol="PMI",
                side="BUY",
                quantity=Decimal("10"),
                order_type="LIMIT",
                limit_price=Decimal("1.00"),
                stop_price=None,
                time_in_force="DAY",
                strategy_lifecycle_id="trade-b",
                metadata={
                    "source": "active-conflict-regression",
                    "lifecycle_id": "trade-b",
                },
            )
        )
    )
    assert conflict.success
    first.close()

    second = create_paper_trading_command_composition(
        persistence_path=str(path),
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    recovered = AutonomousPaperExecutionBridge(
        second.trading_service,
        second.order_command_factory,
        order_book=second.order_book,
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
        management_context_source=lambda _symbol: "trade-b",
    )
    before_history = tuple(second.order_book.history())
    before_events = len(
        performance_diagnostics.reconciliation_metrics()["protection_events"]
    )
    try:
        assert recovered.reconcile() is AutonomousPaperReadiness.BLOCKED
        result = recovered.ensure_exit(
            "PMI", 12, Decimal("10.50"), "FIRST_TARGET", "trade-a",
        )
        assert result.state is PaperExitSubmissionState.UNAVAILABLE
        assert (
            result.failure_reason
            is PaperExitSubmissionFailureReason.BRIDGE_NOT_READY
        )
        assert tuple(second.order_book.history()) == before_history
        durable_stop = second.order_book.get(stop.order_id)
        assert durable_stop.status.value == "ACCEPTED"
        assert durable_stop.is_terminal is False
        assert not any(
            order.request.execution_reason == "FIRST_TARGET"
            for order in second.order_book.history()
        )
        events = performance_diagnostics.reconciliation_metrics()[
            "protection_events"
        ][before_events:]
        assert any(
            event["state"] == "ACTIVE_LIFECYCLE_CONFLICT"
            and event["reason"] == "ACTIVE_ENTRY_LIFECYCLE_MISMATCH"
            for event in events
        )
    finally:
        second.close()


def test_partial_target_reversal_liquidates_reserved_shares_and_latches_stop(tmp_path):
    position = {"PMI": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position[symbol],
        persistence_path=str(tmp_path / "paper.sqlite3"),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position[symbol],
    )
    def quote(sequence, bid, ask, size):
        return composition.gateway.process_market_event(MarketEvent(
            sequence, session_timestamp(sequence), "PMI", "test", MarketEventType.QUOTE,
            QuotePayload(Decimal(bid), Decimal(ask), Decimal(size), Decimal(size)),
        ))
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        quote(1, "9.99", "10", "100")
        position["PMI"] = Decimal("100")
        bridge.ensure_exit("PMI", 100, Decimal("9.5"), "STOP", "trade-a")
        target = bridge.ensure_exit("PMI", 50, Decimal("10.5"), "FIRST_TARGET", "trade-a")
        quote(2, "10.5", "10.51", "20")
        position["PMI"] = Decimal("80")
        # The 30 unfilled target shares must join the downside exit immediately.
        reports = quote(3, "9.9", "9.91", "25")
        assert sum((f.quantity for r in reports for f in r.fills), Decimal(0)) == 25
        assert composition.order_book.get(target.order_id).status.value == "CANCELLED"
        stop = composition.order_book.open_orders()[0]
        assert stop.remaining_quantity == 55
        assert stop.request.metadata["stop_triggered"] is True
        restored = {item.order_id: item for item in composition.durable_store.orders()}
        assert restored[stop.order_id].remaining_quantity == 55
        assert restored[stop.order_id].request.metadata["stop_triggered"] is True
        assert restored[target.order_id].status.value == "CANCELLED"
        position["PMI"] = Decimal("55")
        # Restart from durable partial stop state before the rebound.
        composition.close()
        composition = create_paper_trading_command_composition(
            position_quantity_source=lambda symbol: position[symbol],
            persistence_path=str(tmp_path / "paper.sqlite3"),
        )
        # A rebound above the stop must not deactivate a partially filled exit.
        reports = quote(4, "10.1", "10.11", "100")
        assert sum((f.quantity for r in reports for f in r.fills), Decimal(0)) == 55
        assert not composition.order_book.open_orders()
    finally:
        composition.close()


def test_pcvx_first_target_fill_reconciles_stop_from_67_to_34() -> None:
    position = {"PCVX": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
    )
    signal = Signal(
        symbol="PCVX",
        entry_trigger=Decimal("72.49"),
        stop_price=Decimal("72.24"),
        lifecycle_id="pcvx-profit-management",
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0"),
        ),
        management_context_source=lambda _symbol: signal.lifecycle_id,
        protection_amender=composition.gateway.amend_protective_stop,
    )
    try:
        assert bridge.submit_entry(signal, 67, Decimal("21.44"))
        composition.gateway.process_market_event(MarketEvent(
            1, session_timestamp(1), "PCVX", "pcvx-entry",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("72.48"), Decimal("72.49"),
                Decimal("67"), Decimal("67"),
            ),
        ))
        position["PCVX"] = Decimal("67")
        stop = bridge.ensure_exit(
            "PCVX", 67, Decimal("72.24"), "STOP", signal.lifecycle_id,
        )
        target = bridge.ensure_exit(
            "PCVX", 33, Decimal("72.8325"), "FIRST_TARGET",
            signal.lifecycle_id,
        )
        assert stop.protection_active and target.protection_active

        composition.gateway.process_market_event(MarketEvent(
            2, session_timestamp(2), "PCVX", "pcvx-first-target",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("72.8325"), Decimal("72.84"),
                Decimal("33"), Decimal("33"),
            ),
        ))
        position["PCVX"] = Decimal("34")
        sells = composition.order_book.open_orders_for_symbol("PCVX")
        stops = tuple(
            order for order in sells
            if order.request.order_type is OrderType.STOP
        )
        assert composition.order_book.get(target.order_id).status.value == "FILLED"
        assert len(stops) == 1
        assert stops[0].remaining_quantity == Decimal("34")
        assert stops[0].request.metadata["reservation_mode"] == "ACTIVE_PROTECTION"
        assert all(order.remaining_quantity <= Decimal("34") for order in sells)
    finally:
        composition.close()


def test_chga_hybrid_bracket_preserves_runner_through_giveback() -> None:
    """CHGA-like +1.4R excursion scales out without losing runner protection."""
    position = {"CHGA": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    signal = Signal(
        symbol="CHGA", entry_trigger=Decimal("2.9045"),
        stop_price=Decimal("2.7606"), lifecycle_id="chga-episode",
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    try:
        assert bridge.submit_entry(signal, 322, Decimal("46.35"))
        composition.gateway.process_market_event(MarketEvent(
            1, session_timestamp(1), "CHGA", "chga", MarketEventType.QUOTE,
            QuotePayload(Decimal("2.90"), Decimal("2.9045"), Decimal("322"), Decimal("322")),
        ))
        position["CHGA"] = Decimal("322")
        stop = bridge.ensure_exit("CHGA", 322, signal.stop_price, "STOP", signal.lifecycle_id)
        assert stop.protection_active
        target = bridge.ensure_exit("CHGA", 161, Decimal("3.0484"), "FIRST_TARGET", signal.lifecycle_id)
        assert target.protection_active
        sells = [o for o in composition.order_book.open_orders_for_symbol("CHGA")
                 if o.request.side.value == "SELL"]
        assert {o.request.order_type.value for o in sells} == {"LIMIT", "STOP"}
        assert sum(int(o.remaining_quantity) for o in sells
                   if o.request.order_type.value == "LIMIT") == 161

        stops = [o for o in composition.order_book.open_orders_for_symbol("CHGA")
                 if o.request.side.value == "SELL" and o.request.order_type is OrderType.STOP]
        assert len(stops) == 1 and int(stops[0].remaining_quantity) == 322
    finally:
        composition.close()


def test_incremental_entry_fills_restore_single_first_target_after_stop_replace() -> None:
    """WETO-shaped fills cannot leave an open position targetless."""
    position = {"WETO": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service, composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
        protection_amender=composition.gateway.amend_protective_stop,
    )
    try:
        signal = Signal(
            symbol="WETO", lifecycle_id="weto-episode",
            entry_trigger=Decimal("1.16"), stop_price=Decimal("1.11"),
        )
        assert bridge.submit_entry(signal, 601, Decimal("1.16"))
        position["WETO"] = Decimal("50")
        composition.gateway.process_market_event(MarketEvent(
            1, session_timestamp(1), "WETO", "weto-entry-1",
            MarketEventType.QUOTE,
            QuotePayload(Decimal("1.15"), Decimal("1.16"), Decimal("50"), Decimal("50")),
        ))
        stop = bridge.ensure_exit("WETO", 50, Decimal("1.11"), "STOP", signal.lifecycle_id)
        assert stop.protection_active
        target = bridge.ensure_exit(
            "WETO", 25, Decimal("1.21"), "FIRST_TARGET", signal.lifecycle_id,
        )
        assert target.protection_active
        assert sum(
            1 for order in composition.order_book.open_orders_for_symbol("WETO")
            if order.request.execution_reason == "FIRST_TARGET"
        ) == 1

        # Simulate a sibling cancellation during stop replacement, followed by
        # the remaining entry fills.  Reconciliation must restore one target
        # from the new authoritative inventory, not the original 50 shares.
        target_order = composition.order_book.get(target.order_id)
        assert composition.gateway.cancel_order(OrderCancellationRequest(
            request_id="cancel-weto-target",
            session_id=composition.session_id,
            account_id=composition.account_id,
            broker_order_id=target.order_id,
            client_order_id=target_order.request.client_order_id,
        )).accepted
        position["WETO"] = Decimal("601")
        restored = bridge.ensure_exit(
            "WETO", 300, Decimal("1.21"), "FIRST_TARGET", signal.lifecycle_id,
        )
        assert restored.protection_active
        sells = composition.order_book.open_orders_for_symbol("WETO")
        targets = [o for o in sells if o.request.execution_reason == "FIRST_TARGET"]
        stops = [o for o in sells if o.request.order_type is OrderType.STOP]
        assert len(targets) == 1 and targets[0].remaining_quantity == Decimal("300")
        assert len(stops) == 1 and stops[0].remaining_quantity == Decimal("601")
    finally:
        composition.close()


def test_missing_first_target_is_recreated_for_incremental_ncnA_and_xrpn_positions() -> None:
    """A valid open position always converges to STOP plus FIRST_TARGET."""
    for symbol, quantity in (("NCNA", 404), ("XRPN", 1)):
        position = {symbol: Decimal(str(quantity))}
        composition = create_paper_trading_command_composition(
            position_quantity_source=lambda name, p=position: p.get(name, Decimal("0")),
        )
        bridge = AutonomousPaperExecutionBridge(
            composition.trading_service, composition.order_command_factory,
            order_book=composition.order_book,
            position_quantity_source=lambda name, p=position: p.get(name, Decimal("0")),
            protection_amender=composition.gateway.amend_protective_stop,
        )
        try:
            signal = Signal(symbol=symbol, lifecycle_id=f"{symbol}-episode")
            assert bridge.submit_entry(signal, quantity, Decimal("1.50"))
            stop = bridge.ensure_exit(symbol, quantity, Decimal("1.10"), "STOP", signal.lifecycle_id)
            assert stop.protection_active
            target = bridge.ensure_exit(
                symbol, max(1, quantity // 2), Decimal("1.60"),
                "FIRST_TARGET", signal.lifecycle_id,
            )
            assert target.protection_active
            sells = composition.order_book.open_orders_for_symbol(symbol)
            assert sum(o.request.order_type is OrderType.STOP for o in sells) == 1
            assert sum(o.request.execution_reason == "FIRST_TARGET" for o in sells) == 1
        finally:
            composition.close()


def test_full_runner_target_retains_contingent_stop_through_partial_fill_and_restart(
    tmp_path,
):
    position = {"PMI": Decimal("0")}
    path = tmp_path / "runner-bracket.sqlite3"

    def build():
        composition = create_paper_trading_command_composition(
            position_quantity_source=lambda symbol: position[symbol],
            persistence_path=str(path),
        )
        bridge = AutonomousPaperExecutionBridge(
            composition.trading_service,
            composition.order_command_factory,
            order_book=composition.order_book,
            position_quantity_source=lambda symbol: position[symbol],
            management_context_source=lambda _symbol: "trade-a",
            protection_amender=composition.gateway.amend_protective_stop,
        )
        return composition, bridge

    composition, bridge = build()
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(composition, 1, "9.99", "10")
        position["PMI"] = Decimal("100")
        bridge.ensure_exit("PMI", 100, Decimal("9.5"), "STOP", "trade-a")
        target = bridge.ensure_exit(
            "PMI", 100, Decimal("10.5"), "RUNNER_TARGET", "trade-a",
        )
        sells = composition.order_book.open_orders_for_symbol("PMI")
        stop = next(order for order in sells if order.request.order_type is OrderType.STOP)
        assert stop.request.metadata["reservation_mode"] == "CONTINGENT_OCO"
        assert sum(
            order.remaining_quantity for order in sells
            if order.request.metadata.get("reservation_mode") != "CONTINGENT_OCO"
        ) == Decimal("100")

        reports = composition.gateway.process_market_event(MarketEvent(
            2, session_timestamp(2), "PMI", "runner-partial",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("10.5"), Decimal("10.51"),
                Decimal("30"), Decimal("30"),
            ),
        ))
        assert sum(
            (fill.quantity for report in reports for fill in report.fills),
            Decimal("0"),
        ) == Decimal("30")
        position["PMI"] = Decimal("70")

        reports = composition.gateway.process_market_event(MarketEvent(
            3, session_timestamp(3), "PMI", "runner-stop-race",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("9.49"), Decimal("9.50"),
                Decimal("25"), Decimal("25"),
            ),
        ))
        assert composition.order_book.get(target.order_id).status.value == "CANCELLED"
        assert sum(
            (fill.quantity for report in reports for fill in report.fills),
            Decimal("0"),
        ) == Decimal("25")
        triggered = composition.order_book.open_orders_for_symbol("PMI")[0]
        assert triggered.order_id == stop.order_id
        assert triggered.remaining_quantity == Decimal("45")
        assert triggered.request.metadata["stop_triggered"] is True
        assert triggered.request.metadata["reservation_mode"] == "ACTIVE_PROTECTION"
        position["PMI"] = Decimal("45")

        composition.close()
        composition, _recovered = build()
        reports = composition.gateway.process_market_event(MarketEvent(
            4, session_timestamp(4), "PMI", "runner-stop-restart",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("10.1"), Decimal("10.11"),
                Decimal("100"), Decimal("100"),
            ),
        ))
        assert sum(
            (fill.quantity for report in reports for fill in report.fills),
            Decimal("0"),
        ) == Decimal("45")
        assert composition.order_book.open_orders_for_symbol("PMI") == ()
    finally:
        composition.close()


def test_full_runner_bracket_survives_recovery_and_replaces_after_target_cancel(
    tmp_path,
):
    position = {"PMI": Decimal("0")}
    path = tmp_path / "runner-recovery.sqlite3"

    def build():
        composition = create_paper_trading_command_composition(
            position_quantity_source=lambda symbol: position[symbol],
            persistence_path=str(path),
        )
        bridge = AutonomousPaperExecutionBridge(
            composition.trading_service,
            composition.order_command_factory,
            order_book=composition.order_book,
            position_quantity_source=lambda symbol: position[symbol],
            management_context_source=lambda _symbol: "trade-a",
            protection_amender=composition.gateway.amend_protective_stop,
        )
        return composition, bridge

    first, bridge = build()
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(first, 1, "9.99", "10")
        position["PMI"] = Decimal("100")
        bridge.ensure_exit("PMI", 100, Decimal("9.5"), "STOP", "trade-a")
        runner = bridge.ensure_exit(
            "PMI", 100, Decimal("10.5"), "RUNNER_TARGET", "trade-a",
        )
        sells = first.order_book.open_orders_for_symbol("PMI")
        stop_before_restart = next(
            order for order in sells if order.request.order_type is OrderType.STOP
        )
        # Reproduce a durable target-only restart seam (for example, process
        # loss between the two placements) without editing persistence.
        cancellation = first.gateway.cancel_order(OrderCancellationRequest(
            request_id="cancel-contingent-before-restart",
            session_id=first.session_id,
            account_id=first.account_id,
            broker_order_id=stop_before_restart.order_id,
            client_order_id=stop_before_restart.request.client_order_id,
        ))
        assert cancellation.accepted is True
        assert first.order_book.get(runner.order_id).is_terminal is False
    finally:
        first.close()

    second, recovered = build()
    try:
        assert recovered.reconcile() is AutonomousPaperReadiness.READY
        assert recovered.reconcile_protection() == ("PMI",)
        sells = second.order_book.open_orders_for_symbol("PMI")
        assert {order.request.order_type for order in sells} == {
            OrderType.LIMIT, OrderType.STOP,
        }
        stop = next(order for order in sells if order.request.order_type is OrderType.STOP)
        target = next(order for order in sells if order.request.order_type is OrderType.LIMIT)
        assert stop.request.metadata["correlated_target_order_id"] == target.order_id
        assert stop.order_id != stop_before_restart.order_id

        cancellation = second.gateway.cancel_order(OrderCancellationRequest(
            request_id="cancel-runner-target",
            session_id=second.session_id,
            account_id=second.account_id,
            broker_order_id=target.order_id,
            client_order_id=target.request.client_order_id,
        ))
        assert cancellation.accepted is True
        replacement = recovered.ensure_exit(
            "PMI", 100, Decimal("10.6"), "RUNNER_TARGET", "trade-a",
        )
        assert replacement.protection_active is True
        replaced_sells = second.order_book.open_orders_for_symbol("PMI")
        assert len(replaced_sells) == 2
        replacement_target = next(
            order for order in replaced_sells
            if order.request.order_type is OrderType.LIMIT
        )
        replacement_stop = next(
            order for order in replaced_sells
            if order.request.order_type is OrderType.STOP
        )
        assert replacement_target.request.limit_price == Decimal("10.6")
        assert replacement_stop.order_id != stop.order_id
        assert second.order_book.get(stop.order_id).status.value == "CANCELLED"
        assert sum(
            order.remaining_quantity for order in replaced_sells
            if order.request.metadata.get("reservation_mode") != "CONTINGENT_OCO"
        ) == position["PMI"]
    finally:
        second.close()


def test_legacy_partial_target_bracket_recovery_upgrades_full_contingent_stop(
    tmp_path,
):
    """A target-reserved legacy half may not remain outside stop coverage."""
    position = {"PMI": Decimal("0")}
    path = tmp_path / "legacy-partial-target.sqlite3"

    def build(at=session_timestamp(0)):
        composition = create_paper_trading_command_composition(
            position_quantity_source=lambda symbol: position[symbol],
            persistence_path=str(path),
            at=at,
        )
        bridge = AutonomousPaperExecutionBridge(
            composition.trading_service,
            composition.order_command_factory,
            order_book=composition.order_book,
            position_quantity_source=lambda symbol: position[symbol],
            management_context_source=lambda _symbol: "trade-a",
            protection_amender=composition.gateway.amend_protective_stop,
        )
        return composition, bridge

    first, bridge = build()
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(first, 1, "9.99", "10")
        position["PMI"] = Decimal("100")
        target_result = first.trading_service.place_order(
            first.order_command_factory.create_placement_request(
                OrderEntryCommand(
                    symbol="PMI", side="SELL", quantity=Decimal("50"),
                    order_type="LIMIT", limit_price=Decimal("10.5"),
                    stop_price=None, time_in_force="GTC",
                    strategy_lifecycle_id="trade-a",
                    metadata={"source": "legacy-fixture", "reason": "FIRST_TARGET"},
                )
            )
        )
        stop_result = first.trading_service.place_order(
            first.order_command_factory.create_placement_request(
                OrderEntryCommand(
                    symbol="PMI", side="SELL", quantity=Decimal("50"),
                    order_type="STOP", limit_price=None,
                    stop_price=Decimal("9.5"), time_in_force="GTC",
                    strategy_lifecycle_id="trade-a",
                    metadata={"source": "legacy-fixture", "reason": "STOP"},
                )
            )
        )
        assert target_result.success and stop_result.success
        target = first.order_book.get(target_result.broker_order_id)
        legacy_sells = first.order_book.open_orders_for_symbol("PMI")
        legacy_stop = next(
            order for order in legacy_sells
            if order.request.order_type is OrderType.STOP
        )
        assert legacy_stop.remaining_quantity == Decimal("50")
        assert legacy_stop.request.metadata.get("reservation_mode") is None
        reports = first.gateway.process_market_event(MarketEvent(
            2, session_timestamp(2), "PMI", "legacy-target-partial",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("10.5"), Decimal("10.51"),
                Decimal("20"), Decimal("20"),
            ),
        ))
        assert sum(
            (fill.quantity for report in reports for fill in report.fills),
            Decimal("0"),
        ) == Decimal("20")
        position["PMI"] = Decimal("80")
    finally:
        first.close()

    second, recovered = build(session_timestamp(3))
    try:
        assert recovered.reconcile() is AutonomousPaperReadiness.READY
        assert recovered.reconcile_protection() == ("PMI",)
        sells = second.order_book.open_orders_for_symbol("PMI")
        stop = next(
            order for order in sells if order.request.order_type is OrderType.STOP
        )
        restored_target = next(
            order for order in sells if order.request.order_type is OrderType.LIMIT
        )
        assert restored_target.order_id == target.order_id
        assert stop.order_id == legacy_stop.order_id
        assert stop.remaining_quantity == Decimal("80")
        assert stop.request.metadata["reservation_mode"] == "CONTINGENT_OCO"
        assert stop.request.metadata["correlated_target_order_id"] == target.order_id
        assert sum(
            order.remaining_quantity for order in sells
            if order.request.metadata.get("reservation_mode") != "CONTINGENT_OCO"
        ) == Decimal("30")

        # Reconciliation is idempotent: it neither replaces the recovered
        # stop nor appends another durable order.
        history_count = len(second.order_book.history())
        assert recovered.reconcile_protection() == ("PMI",)
        assert len(second.order_book.history()) == history_count
        assert next(
            order for order in second.order_book.open_orders_for_symbol("PMI")
            if order.request.order_type is OrderType.STOP
        ).order_id == legacy_stop.order_id

        cancellation = second.gateway.cancel_order(OrderCancellationRequest(
            request_id="cancel-recovered-legacy-target",
            session_id=second.session_id,
            account_id=second.account_id,
            broker_order_id=restored_target.order_id,
            client_order_id=restored_target.request.client_order_id,
        ))
        assert cancellation.accepted
        assert recovered.reconcile_protection() == ("PMI",)
        active_stop = second.order_book.open_orders_for_symbol("PMI")[0]
        assert active_stop.order_id == legacy_stop.order_id
        assert active_stop.remaining_quantity == Decimal("80")
        assert active_stop.request.metadata["reservation_mode"] == "ACTIVE_PROTECTION"
        assert "correlated_target_order_id" not in active_stop.request.metadata

        replacement = recovered.ensure_exit(
            "PMI", 40, Decimal("10.6"), "RUNNER_TARGET", "trade-a",
        )
        assert replacement.protection_active
        replacement_sells = second.order_book.open_orders_for_symbol("PMI")
        replacement_target = next(
            order for order in replacement_sells
            if order.request.order_type is OrderType.LIMIT
        )
        replacement_stop = next(
            order for order in replacement_sells
            if order.request.order_type is OrderType.STOP
        )
        assert replacement_target.remaining_quantity == Decimal("40")
        assert replacement_stop.remaining_quantity == Decimal("80")
        assert replacement_stop.request.metadata["correlated_target_order_id"] == (
            replacement_target.order_id
        )
        assert sum(
            order.remaining_quantity for order in replacement_sells
            if order.request.metadata.get("reservation_mode") != "CONTINGENT_OCO"
        ) == Decimal("40")
    finally:
        second.close()


def test_recovered_valid_bracket_is_idempotent_when_stop_sorts_before_target(
    tmp_path,
):
    """Startup must not duplicate a target because opaque IDs restore stop first."""
    position = {"PMI": Decimal("0")}
    path = tmp_path / "valid-correlated-bracket.sqlite3"

    def build():
        composition = create_paper_trading_command_composition(
            position_quantity_source=lambda symbol: position[symbol],
            persistence_path=str(path),
        )
        bridge = AutonomousPaperExecutionBridge(
            composition.trading_service,
            composition.order_command_factory,
            order_book=composition.order_book,
            position_quantity_source=lambda symbol: position[symbol],
            management_context_source=lambda _symbol: "trade-a",
            protection_amender=composition.gateway.amend_protective_stop,
        )
        return composition, bridge

    first, bridge = build()
    try:
        target_id = "PAPER-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
        stop_id = "PAPER-00000000000000000000000000000001"
        with patch(
            "app.paper_trading.orders._new_order_id",
            side_effect=(
                "PAPER-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
                target_id,
                stop_id,
            ),
        ):
            assert bridge.submit_entry(Signal(), 100, Decimal("50"))
            _paper_quote(first, 1, "9.99", "10")
            position["PMI"] = Decimal("100")
            target_result = first.trading_service.place_order(
                first.order_command_factory.create_placement_request(
                    OrderEntryCommand(
                        symbol="PMI", side="SELL", quantity=Decimal("50"),
                        order_type="LIMIT", limit_price=Decimal("10.5"),
                        stop_price=None, time_in_force="GTC",
                        strategy_lifecycle_id="trade-a",
                        metadata={
                            "source": "recovery-fixture",
                            "reason": "FIRST_TARGET",
                        },
                    )
                )
            )
            assert target_result.success
            assert target_result.broker_order_id == target_id
            stop_result = first.trading_service.place_order(
                first.order_command_factory.create_placement_request(
                    OrderEntryCommand(
                        symbol="PMI", side="SELL", quantity=Decimal("100"),
                        order_type="STOP", limit_price=None,
                        stop_price=Decimal("9.5"), time_in_force="GTC",
                        strategy_lifecycle_id="trade-a",
                        metadata={
                            "source": "recovery-fixture", "reason": "STOP",
                            "reservation_mode": "CONTINGENT_OCO",
                            "correlated_target_order_id": target_id,
                        },
                    )
                )
            )
            assert stop_result.success
            assert stop_result.broker_order_id == stop_id
    finally:
        first.close()

    second, recovered = build()
    try:
        assert recovered.reconcile() is AutonomousPaperReadiness.READY
        assert recovered._exit_orders[("trade-a", "FIRST_TARGET")] == target_id
        assert recovered._exit_orders[("trade-a", "STOP")] == stop_id
        history_before = tuple(second.order_book.history())
        events_before = tuple(second.durable_store.events())
        watermark_before = second.durable_store.event_sequence_watermark

        assert recovered.reconcile_protection() == ("PMI",)
        assert recovered.reconcile_protection() == ("PMI",)
        result = recovered.ensure_exit(
            "PMI", 50, Decimal("10.5"), "FIRST_TARGET", "trade-a",
        )

        assert result.state.value == "WORKING"
        assert result.order_id == target_id
        assert tuple(second.order_book.history()) == history_before
        assert tuple(second.durable_store.events()) == events_before
        assert second.durable_store.event_sequence_watermark == watermark_before
        sells = second.order_book.open_orders_for_symbol("PMI")
        assert {order.order_id for order in sells} == {target_id, stop_id}
        restored_stop = next(
            order for order in sells
            if order.request.order_type is OrderType.STOP
        )
        assert restored_stop.remaining_quantity == Decimal("100")
        assert restored_stop.request.metadata["reservation_mode"] == (
            "CONTINGENT_OCO"
        )
        assert restored_stop.request.metadata[
            "correlated_target_order_id"
        ] == target_id
    finally:
        second.close()


def test_legacy_bracket_upgrade_persistence_failure_is_fail_closed_and_recoverable(
    tmp_path,
):
    position = {"PMI": Decimal("0")}
    path = tmp_path / "legacy-upgrade-crash.sqlite3"

    def build():
        composition = create_paper_trading_command_composition(
            position_quantity_source=lambda symbol: position[symbol],
            persistence_path=str(path),
        )
        return composition, AutonomousPaperExecutionBridge(
            composition.trading_service,
            composition.order_command_factory,
            order_book=composition.order_book,
            position_quantity_source=lambda symbol: position[symbol],
            management_context_source=lambda _symbol: "trade-a",
            protection_amender=composition.gateway.amend_protective_stop,
        )

    first, bridge = build()
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(first, 1, "9.99", "10")
        position["PMI"] = Decimal("100")
        for command in (
            OrderEntryCommand(
                symbol="PMI", side="SELL", quantity=Decimal("50"),
                order_type="LIMIT", limit_price=Decimal("10.5"),
                stop_price=None, time_in_force="GTC",
                strategy_lifecycle_id="trade-a",
                metadata={"source": "legacy-fixture", "reason": "FIRST_TARGET"},
            ),
            OrderEntryCommand(
                symbol="PMI", side="SELL", quantity=Decimal("50"),
                order_type="STOP", limit_price=None, stop_price=Decimal("9.5"),
                time_in_force="GTC", strategy_lifecycle_id="trade-a",
                metadata={"source": "legacy-fixture", "reason": "STOP"},
            ),
        ):
            assert first.trading_service.place_order(
                first.order_command_factory.create_placement_request(command)
            ).success
    finally:
        first.close()

    failed, recovery = build()
    legacy_stop = next(
        order for order in failed.order_book.open_orders_for_symbol("PMI")
        if order.request.order_type is OrderType.STOP
    )
    try:
        assert recovery.reconcile() is AutonomousPaperReadiness.READY
        store_type = type(failed.durable_store)
        with patch.object(store_type, "persist", side_effect=OSError("disk full")):
            assert recovery.reconcile_protection() == ()
        unchanged = failed.order_book.get(legacy_stop.order_id)
        assert unchanged.remaining_quantity == Decimal("50")
        assert unchanged.request.metadata.get("reservation_mode") is None
        persisted = {
            order.order_id: order for order in failed.durable_store.orders()
        }[legacy_stop.order_id]
        assert persisted.remaining_quantity == Decimal("50")
        assert persisted.request.metadata.get("reservation_mode") is None
    finally:
        failed.close()

    restarted, recovered = build()
    try:
        assert recovered.reconcile() is AutonomousPaperReadiness.READY
        assert recovered.reconcile_protection() == ("PMI",)
        stop = next(
            order for order in restarted.order_book.open_orders_for_symbol("PMI")
            if order.request.order_type is OrderType.STOP
        )
        assert stop.order_id == legacy_stop.order_id
        assert stop.remaining_quantity == Decimal("100")
        assert stop.request.metadata["reservation_mode"] == "CONTINGENT_OCO"
    finally:
        restarted.close()


def test_full_runner_target_fill_cannot_race_contingent_stop_into_oversell():
    position = {"PMI": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position[symbol],
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position[symbol],
        management_context_source=lambda _symbol: "trade-a",
    )
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(composition, 1, "9.99", "10")
        position["PMI"] = Decimal("100")
        bridge.ensure_exit("PMI", 100, Decimal("9.5"), "STOP", "trade-a")
        target = bridge.ensure_exit(
            "PMI", 100, Decimal("10.5"), "RUNNER_TARGET", "trade-a",
        )
        reports = composition.gateway.process_market_event(MarketEvent(
            2, session_timestamp(2), "PMI", "runner-target-win",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("10.5"), Decimal("10.51"),
                Decimal("100"), Decimal("100"),
            ),
        ))
        assert sum(
            (fill.quantity for report in reports for fill in report.fills),
            Decimal("0"),
        ) == Decimal("100")
        assert composition.order_book.get(target.order_id).status.value == "FILLED"
        position["PMI"] = Decimal("0")

        reports = composition.gateway.process_market_event(MarketEvent(
            3, session_timestamp(3), "PMI", "runner-post-fill-stop",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("9.49"), Decimal("9.50"),
                Decimal("100"), Decimal("100"),
            ),
        ))
        assert not any(report.fills for report in reports)
        assert composition.order_book.open_orders_for_symbol("PMI") == ()
    finally:
        composition.close()


def test_filled_target_cannot_be_reissued_after_recovery():
    position = {"PMI": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position[symbol])
    def build():
        return AutonomousPaperExecutionBridge(composition.trading_service,
            composition.order_command_factory, order_book=composition.order_book,
            position_quantity_source=lambda symbol: position[symbol],
            management_context_source=lambda _: "trade-a")
    bridge = build()
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(composition, 1, "9.99", "10")
        position["PMI"] = Decimal("100")
        bridge.ensure_exit("PMI", 100, Decimal("9.5"), "STOP", "trade-a")
        bridge.ensure_exit("PMI", 50, Decimal("10.5"), "FIRST_TARGET", "trade-a")
        _paper_quote(composition, 2, "10.5", "10.51")
        position["PMI"] = Decimal("50")
        recovered = build()
        recovered.begin_reconciliation()
        recovered.reconcile()
        recovered.reconcile_protection()
        count = len(composition.order_book.history())
        result = recovered.ensure_exit("PMI", 25, Decimal("10.5"), "FIRST_TARGET", "trade-a")
        assert result.state.value == "COMPLETED"
        assert len(composition.order_book.history()) == count
    finally:
        composition.close()


@pytest.mark.parametrize(
    ("bridge_kwargs", "symbol", "quantity", "expected"),
    (
        ({}, "", 1, PaperExitSubmissionFailureReason.INVALID_EXIT_REQUEST),
        ({"mode": "LIVE"}, "PMI", 1, PaperExitSubmissionFailureReason.PAPER_DISABLED),
        ({"position_quantity_source": lambda _symbol: Decimal("1")}, "PMI", 2,
         PaperExitSubmissionFailureReason.POSITION_QUANTITY_INSUFFICIENT),
    ),
)
def test_ensure_exit_reports_pre_gateway_failure_subtypes(
    bridge_kwargs, symbol, quantity, expected,
) -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        **bridge_kwargs,
    )
    try:
        result = bridge.ensure_exit(
            symbol, quantity, Decimal("10.50"), "FIRST_TARGET", "trade-a",
        )
        assert result.state is PaperExitSubmissionState.UNAVAILABLE
        assert result.role == "FIRST_TARGET"
        assert result.failure_reason is expected
        assert composition.order_book.history() == ()
    finally:
        composition.close()


def test_ensure_exit_reports_bridge_readiness_management_lifecycle_and_gateway() -> None:
    position = {"PMI": Decimal("100")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    try:
        bridge.begin_reconciliation()
        unavailable = bridge.ensure_exit(
            "PMI", 1, Decimal("10.50"), "FIRST_TARGET", "trade-a",
        )
        assert unavailable.failure_reason is PaperExitSubmissionFailureReason.BRIDGE_NOT_READY

        assert bridge.reconcile() is AutonomousPaperReadiness.READY
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(composition, 1, "9.99", "10")

        bridge._management_incomplete.add("PMI")
        not_ready = bridge.ensure_exit(
            "PMI", 50, Decimal("10.50"), "FIRST_TARGET", "trade-a",
        )
        assert not_ready.failure_reason is PaperExitSubmissionFailureReason.MANAGEMENT_NOT_READY
        bridge._management_incomplete.discard("PMI")

        mismatch = bridge.ensure_exit(
            "PMI", 50, Decimal("10.50"), "FIRST_TARGET", "trade-b",
        )
        assert mismatch.failure_reason is PaperExitSubmissionFailureReason.LIFECYCLE_MISMATCH

        with patch.object(
            bridge.trading_service, "place_order",
            return_value=SimpleNamespace(success=False, decision="REJECTED"),
        ):
            rejected = bridge.ensure_exit(
                "PMI", 50, Decimal("10.50"), "FIRST_TARGET", "trade-a",
            )
        assert rejected.failure_reason is PaperExitSubmissionFailureReason.GATEWAY_REJECTED
    finally:
        composition.close()


def test_target_cancellation_failure_is_typed_and_preserves_existing_stop() -> None:
    position = {"PMI": Decimal("100")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(composition, 1, "9.99", "10")
        stop = bridge.ensure_exit(
            "PMI", 100, Decimal("9.50"), "STOP", "trade-a",
        )
        before = tuple(composition.order_book.history())
        with patch.object(
            AutonomousPaperExecutionBridge,
            "_cancel_working_order",
            return_value=False,
        ):
            target = bridge.ensure_exit(
                "PMI", 50, Decimal("10.50"), "FIRST_TARGET", "trade-a",
            )
        assert target.state is PaperExitSubmissionState.WORKING
        assert target.role == "FIRST_TARGET"
        assert target.failure_reason is PaperExitSubmissionFailureReason.CANCELLATION_FAILED
        assert tuple(composition.order_book.history()) == before
        assert composition.order_book.get(stop.order_id).is_terminal is False
    finally:
        composition.close()


def test_ensure_exit_reports_missing_position_source_and_triggered_stop_conflict() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
    )
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(composition, 1, "9.99", "10")
        stop = bridge.ensure_exit(
            "PMI", 100, Decimal("9.50"), "STOP", "trade-a",
        )
        missing_source = bridge.ensure_exit(
            "PMI", 50, Decimal("10.50"), "FIRST_TARGET", "trade-a",
        )
        assert (
            missing_source.failure_reason
            is PaperExitSubmissionFailureReason.POSITION_SOURCE_UNAVAILABLE
        )
        stop_order = composition.order_book.get(stop.order_id)
        composition.order_book.update(replace(
            stop_order,
            request=replace(
                stop_order.request,
                metadata={**stop_order.request.metadata, "stop_triggered": True},
            ),
        ))
        conflict = bridge.ensure_exit(
            "PMI", 50, Decimal("10.50"), "FIRST_TARGET", "trade-a",
        )
        assert (
            conflict.failure_reason
            is PaperExitSubmissionFailureReason.DUPLICATE_OR_CONFLICTING_EXIT
        )
    finally:
        composition.close()


def test_ensure_exit_reports_reconciliation_and_correlated_protection_failures() -> None:
    position = {"PMI": Decimal("100")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(composition, 1, "9.99", "10")
        with patch.object(
            AutonomousPaperExecutionBridge,
            "_reconcile_correlated_exits",
            return_value=False,
        ):
            conflict = bridge.ensure_exit(
                "PMI", 50, Decimal("10.50"), "FIRST_TARGET", "trade-a",
            )
        assert (
            conflict.failure_reason
            is PaperExitSubmissionFailureReason.DUPLICATE_OR_CONFLICTING_EXIT
        )
        bridge._management_incomplete.discard("PMI")
        with patch.object(
            AutonomousPaperExecutionBridge,
            "_reconcile_protective_quantity",
            return_value=False,
        ):
            protection = bridge.ensure_exit(
                "PMI", 100, Decimal("9.50"), "STOP", "trade-a",
            )
        assert (
            protection.failure_reason
            is PaperExitSubmissionFailureReason.PROTECTION_RECONCILIATION_FAILED
        )
    finally:
        composition.close()


def test_exit_failure_diagnostic_exception_does_not_change_decision() -> None:
    composition = create_paper_trading_command_composition()
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
    )
    try:
        with patch(
            "app.strategies.warrior_momentum.autonomous_paper."
            "performance_diagnostics.record_management_event",
            side_effect=RuntimeError("diagnostic unavailable"),
        ):
            result = bridge.ensure_exit(
                "", 1, Decimal("10.50"), "FIRST_TARGET", "trade-a",
            )
        assert result.state is PaperExitSubmissionState.UNAVAILABLE
        assert result.failure_reason is PaperExitSubmissionFailureReason.INVALID_EXIT_REQUEST
    finally:
        composition.close()


def test_correlated_protection_failure_has_distinct_subtype() -> None:
    position = {"PMI": Decimal("100")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(symbol, Decimal("0")),
    )
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(composition, 1, "9.99", "10")
        assert bridge.ensure_exit(
            "PMI", 100, Decimal("9.50"), "STOP", "trade-a",
        ).protection_active

        def placement(
            _bridge, normalized, quantity, price, reason_key, identity,
            *, contingent_target_order_id=None,
        ):
            del quantity, price
            if reason_key == "FIRST_TARGET":
                return PaperExitSubmissionDecision(
                    PaperExitSubmissionState.SUBMITTED,
                    normalized,
                    identity,
                    reason_key,
                    "TEST-TARGET",
                )
            if contingent_target_order_id is not None:
                return PaperExitSubmissionDecision(
                    PaperExitSubmissionState.UNAVAILABLE,
                    normalized,
                    identity,
                    reason_key,
                )
            return PaperExitSubmissionDecision(
                PaperExitSubmissionState.SUBMITTED,
                normalized,
                identity,
                reason_key,
                "RESTORED-STOP",
            )

        with patch.object(
            AutonomousPaperExecutionBridge,
            "_place_exit",
            autospec=True,
            side_effect=placement,
        ):
            result = bridge.ensure_exit(
                "PMI", 50, Decimal("10.50"), "FIRST_TARGET", "trade-a",
            )
        assert result.state is PaperExitSubmissionState.UNAVAILABLE
        assert (
            result.failure_reason
            is PaperExitSubmissionFailureReason.CORRELATED_PROTECTION_FAILED
        )
    finally:
        composition.close()


def test_incremental_entry_fills_amend_one_stop_without_cancel_churn():
    position = {"PMI": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position[symbol])
    bridge = AutonomousPaperExecutionBridge(composition.trading_service,
        composition.order_command_factory, order_book=composition.order_book,
        position_quantity_source=lambda symbol: position[symbol],
        protection_amender=composition.gateway.amend_protective_stop)
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        ids = []
        for sequence in range(1, 11):
            composition.gateway.process_market_event(MarketEvent(
                sequence, session_timestamp(sequence), "PMI", "test", MarketEventType.QUOTE,
                QuotePayload(Decimal("9.99"), Decimal("10"), Decimal("10"), Decimal("10"))))
            position["PMI"] = Decimal(sequence * 10)
            result = bridge.ensure_exit("PMI", sequence * 10, Decimal("9.5"), "STOP", "trade-a")
            ids.append(result.order_id)
            assert composition.order_book.get(result.order_id).remaining_quantity == sequence * 10
        assert len(set(ids)) == 1
        assert len(composition.order_book.history()) == 2
        assert not composition.gateway.amend_protective_stop(ids[0], 101, Decimal("9.5"))
        assert not composition.gateway.amend_protective_stop(ids[0], 100, Decimal("9.4"))
    finally:
        composition.close()


def test_bracket_persistence_failure_does_not_mutate_orders(tmp_path):
    position = {"PMI": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position[symbol],
        persistence_path=str(tmp_path / "paper.sqlite3"))
    bridge = AutonomousPaperExecutionBridge(composition.trading_service,
        composition.order_command_factory, order_book=composition.order_book,
        position_quantity_source=lambda symbol: position[symbol])
    try:
        assert bridge.submit_entry(Signal(), 100, Decimal("50"))
        _paper_quote(composition, 1, "9.99", "10")
        position["PMI"] = Decimal("100")
        bridge.ensure_exit("PMI", 100, Decimal("9.5"), "STOP", "trade-a")
        bridge.ensure_exit("PMI", 50, Decimal("10.5"), "FIRST_TARGET", "trade-a")
        before = tuple(composition.order_book.history())
        store_type = type(composition.durable_store)
        with patch.object(store_type, "persist_batch", side_effect=OSError("disk full")):
            assert not _paper_quote(composition, 2, "9.9", "9.91")
        assert composition.gateway._durability_error is not None
        assert not _paper_quote(composition, 3, "10.5", "10.51")
        assert tuple(composition.order_book.history()) == before
        persisted = {o.order_id: o for o in composition.durable_store.orders()}
        for order in before:
            assert persisted[order.order_id].status == order.status
            assert persisted[order.order_id].remaining_quantity == order.remaining_quantity
    finally:
        composition.close()


def test_final_target_fill_cancels_correlated_stop_at_zero_position() -> None:
    """A final target fill must not leave a contingent STOP after inventory reaches zero."""
    position = {"PMI": Decimal("0")}

    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0")
        ),
    )

    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0")
        ),
    )

    def full_quote(sequence: int, bid: str, ask: str) -> None:
        composition.gateway.process_market_event(
            MarketEvent(
                sequence,
                session_timestamp(sequence),
                "PMI",
                "test",
                MarketEventType.QUOTE,
                QuotePayload(
                    Decimal(bid),
                    Decimal(ask),
                    Decimal("100"),
                    Decimal("100"),
                ),
            )
        )

    try:
        # XRPN-shaped allocation:
        # 25 shares -> first target 23 -> second target 2 -> flat.
        assert bridge.submit_entry(
            Signal(),
            25,
            Decimal("50"),
        )

        full_quote(1, "9.99", "10.00")
        position["PMI"] = Decimal("25")

        stop = bridge.ensure_exit(
            "PMI",
            25,
            Decimal("9.50"),
            "STOP",
            "trade-a",
        )
        assert stop.protection_active

        first = bridge.ensure_exit(
            "PMI",
            23,
            Decimal("10.50"),
            "FIRST_TARGET",
            "trade-a",
        )
        assert first.protection_active

        sells = tuple(
            order
            for order in composition.order_book.open_orders_for_symbol("PMI")
            if order.request.side.value == "SELL"
        )
        assert len(sells) == 2
        assert sorted(
            (order.request.order_type.value, int(order.remaining_quantity))
            for order in sells
        ) == [
            ("LIMIT", 23),
            ("STOP", 25),
        ]

        # Fill FIRST_TARGET completely, leaving two shares.
        full_quote(2, "10.50", "10.51")
        position["PMI"] = Decimal("2")

        sells = tuple(
            order
            for order in composition.order_book.open_orders_for_symbol("PMI")
            if order.request.side.value == "SELL"
        )

        assert len(sells) == 1
        remainder_stop = sells[0]
        assert remainder_stop.request.order_type.value == "STOP"
        assert int(remainder_stop.remaining_quantity) == 2
        assert (
            remainder_stop.request.metadata.get("reservation_mode")
            == "ACTIVE_PROTECTION"
        )
        assert (
            remainder_stop.request.metadata.get(
                "correlated_target_order_id"
            )
            is None
        )

        second = bridge.ensure_exit(
            "PMI",
            2,
            Decimal("11.00"),
            "SECOND_TARGET",
            "trade-a",
        )
        assert second.protection_active

        sells = tuple(
            order
            for order in composition.order_book.open_orders_for_symbol("PMI")
            if order.request.side.value == "SELL"
        )

        assert sorted(
            (order.request.order_type.value, int(order.remaining_quantity))
            for order in sells
        ) == [
            ("LIMIT", 2),
            ("STOP", 2),
        ]

        second_target = next(
            order
            for order in sells
            if order.request.order_type.value == "LIMIT"
        )
        correlated_stop = next(
            order
            for order in sells
            if order.request.order_type.value == "STOP"
        )

        assert (
            correlated_stop.request.metadata.get("reservation_mode")
            == "CONTINGENT_OCO"
        )
        assert (
            correlated_stop.request.metadata.get(
                "correlated_target_order_id"
            )
            == second_target.order_id
        )

        # Final target fills two shares. The gateway must synchronously
        # cancel the correlated stop in the same processing cycle.
        full_quote(3, "11.00", "11.01")
        position["PMI"] = Decimal("0")

        open_sells = tuple(
            order
            for order in composition.order_book.open_orders_for_symbol("PMI")
            if order.request.side.value == "SELL"
        )
        assert open_sells == ()

        durable_stop = composition.order_book.get(
            correlated_stop.order_id
        )
        durable_target = composition.order_book.get(
            second_target.order_id
        )

        assert durable_target.is_terminal
        assert durable_target.status.value == "FILLED"

        assert durable_stop.is_terminal
        assert durable_stop.status.value == "CANCELLED"
        assert durable_stop.filled_quantity == Decimal("0")
        assert durable_stop.remaining_quantity == Decimal("2")

        # No exit may be recreated once the lifecycle inventory is flat.
        bridge.reconcile_protection()

        assert tuple(
            order
            for order in composition.order_book.open_orders_for_symbol("PMI")
            if order.request.side.value == "SELL"
        ) == ()

    finally:
        composition.close()


def test_rubi_partial_entry_full_target_cancels_residual_buy_before_release() -> None:
    """A flat filled lot cannot be reopened by its residual 1,073-share BUY."""

    position = {"PMI": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0")
        ),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0")
        ),
        management_context_source=lambda _symbol: "rubi-generation",
    )
    signal = Signal(
        entry_trigger=Decimal("1.250625"),
        stop_price=Decimal("1.211375"),
        lifecycle_id="rubi-generation",
    )

    try:
        assert bridge.submit_entry(signal, 1224, Decimal("50")) is True
        entry_reports = composition.gateway.process_market_event(MarketEvent(
            1,
            session_timestamp(1),
            "PMI",
            "test",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("1.24"), Decimal("1.250625"),
                Decimal("1000"), Decimal("10"),
            ),
        ))
        assert entry_reports and entry_reports[0].fills
        entry = next(
            order for order in composition.order_book.history()
            if order.request.side.value == "BUY"
        )
        assert int(entry.filled_quantity) == 10
        assert int(entry.remaining_quantity) == 1214
        position["PMI"] = Decimal("10")

        assert bridge.ensure_exit(
            "PMI", 10, Decimal("1.211375"), "STOP", "rubi-generation",
        ).protection_active
        assert bridge.ensure_exit(
            "PMI", 10, Decimal("1.289875"), "FIRST_TARGET",
            "rubi-generation",
        ).protection_active

        target_reports = composition.gateway.process_market_event(MarketEvent(
            2,
            session_timestamp(2),
            "PMI",
            "test",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("1.30"), Decimal("1.31"),
                Decimal("1000"), Decimal("1000"),
            ),
        ))
        assert target_reports and any(report.fills for report in target_reports)
        position["PMI"] = Decimal("0")

        assert bridge.has_execution_ownership("PMI") is False
        entry = composition.order_book.get(entry.order_id)
        assert entry.is_terminal
        assert int(entry.filled_quantity) == 10
        assert composition.order_book.open_orders_for_symbol("PMI") == ()
    finally:
        composition.close()


def test_aifa_residual_stop_amend_preserves_working_second_target() -> None:
    """A contingent target must not make its full-size stop look oversold."""

    position = {"PMI": Decimal("0")}
    composition = create_paper_trading_command_composition(
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0")
        ),
    )
    bridge = AutonomousPaperExecutionBridge(
        composition.trading_service,
        composition.order_command_factory,
        order_book=composition.order_book,
        position_quantity_source=lambda symbol: position.get(
            symbol, Decimal("0")
        ),
        protection_amender=composition.gateway.amend_protective_stop,
    )
    signal = Signal(
        entry_trigger=Decimal("7.903950"),
        stop_price=Decimal("7.52"),
        lifecycle_id="aifa-generation",
    )

    try:
        assert bridge.submit_entry(signal, 126, Decimal("48.3777")) is True
        reports = composition.gateway.process_market_event(MarketEvent(
            1,
            session_timestamp(1),
            "PMI",
            "test",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("7.82"), Decimal("7.903950"),
                Decimal("1000"), Decimal("1000"),
            ),
        ))
        assert reports and any(report.fills for report in reports)
        position["PMI"] = Decimal("126")

        assert bridge.ensure_exit(
            "PMI", 126, Decimal("7.52"), "STOP", "aifa-generation",
        ).protection_active
        assert bridge.ensure_exit(
            "PMI", 63, Decimal("8.287900"), "FIRST_TARGET",
            "aifa-generation",
        ).protection_active

        fills = composition.gateway.process_market_event(MarketEvent(
            2,
            session_timestamp(2),
            "PMI",
            "test",
            MarketEventType.QUOTE,
            QuotePayload(
                Decimal("8.38"), Decimal("8.45"),
                Decimal("1000"), Decimal("1000"),
            ),
        ))
        assert fills and any(report.fills for report in fills)
        position["PMI"] = Decimal("63")

        assert bridge.ensure_exit(
            "PMI", 31, Decimal("8.671850"), "SECOND_TARGET",
            "aifa-generation",
        ).protection_active
        amended = bridge.ensure_exit(
            "PMI", 63, Decimal("7.903950"), "STOP",
            "aifa-generation",
        )
        assert amended.protection_active

        open_sells = tuple(
            order
            for order in composition.order_book.open_orders_for_symbol("PMI")
            if order.request.side.value == "SELL"
        )
        assert len(open_sells) == 2
        target = next(
            order for order in open_sells
            if order.request.order_type.value == "LIMIT"
        )
        stop = next(
            order for order in open_sells
            if order.request.order_type.value == "STOP"
        )
        assert target.request.execution_reason == "SECOND_TARGET"
        assert int(target.remaining_quantity) == 31
        assert int(stop.remaining_quantity) == 63
        assert stop.request.stop_price == Decimal("7.903950")
        assert (
            stop.request.metadata.get("correlated_target_order_id")
            == target.order_id
        )
    finally:
        composition.close()

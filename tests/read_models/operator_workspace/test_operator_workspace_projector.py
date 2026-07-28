from app.operations_core import (
    OperationsBus,
    OperatorDecisionSelected,
    OperatorSymbolSelected,
    OperatorTimelineSelected,
    OperatorTradeSelected,
)
from app.read_models.operator_workspace import (
    OperatorWorkspaceProjector,
    WorkspaceSelectionSource,
)


def test_projector_propagates_selections_and_clears_stale_specific_state() -> None:
    bus = OperationsBus()
    projector = OperatorWorkspaceProjector(bus)
    try:
        bus.publish(
            OperatorDecisionSelected(
                symbol="AAPL",
                decision_id="decision-1",
            )
        )
        decision = projector.snapshot()
        assert decision.selected_symbol == "AAPL"
        assert decision.selected_decision == "decision-1"
        assert decision.selection_source is WorkspaceSelectionSource.DECISION

        bus.publish(OperatorTradeSelected(symbol="MSFT"))
        trade = projector.snapshot()
        assert trade.selected_symbol == "MSFT"
        assert trade.selected_trade == "MSFT"
        assert trade.selected_decision is None
        assert trade.selection_source is WorkspaceSelectionSource.TRADE

        bus.publish(
            OperatorSymbolSelected(
                symbol="NVDA",
                selection_source="ORDER",
                selection_id="order-7",
            )
        )
        order = projector.snapshot()
        assert order.selected_symbol == "NVDA"
        assert order.selected_order == "order-7"
        assert order.selected_trade is None
        assert order.selection_source is WorkspaceSelectionSource.ORDER
    finally:
        projector.close()


def test_timeline_selection_without_symbol_preserves_symbol_context() -> None:
    bus = OperationsBus()
    projector = OperatorWorkspaceProjector(bus)
    try:
        bus.publish(
            OperatorSymbolSelected(
                symbol="AAPL",
                selection_source="POSITION",
            )
        )
        bus.publish(
            OperatorTimelineSelected(
                timeline_entry_id="timeline-1",
            )
        )

        snapshot = projector.snapshot()
        assert snapshot.selected_symbol == "AAPL"
        assert snapshot.selected_timeline_entry == "timeline-1"
        assert snapshot.selected_position is None
        assert snapshot.selection_source is WorkspaceSelectionSource.TIMELINE
    finally:
        projector.close()


def test_close_is_idempotent_and_stops_projection() -> None:
    bus = OperationsBus()
    projector = OperatorWorkspaceProjector(bus)

    assert bus.subscription_count == 1
    projector.close()
    projector.close()
    bus.publish(OperatorTradeSelected(symbol="AAPL"))

    assert bus.subscription_count == 0
    assert projector.snapshot().selected_symbol is None

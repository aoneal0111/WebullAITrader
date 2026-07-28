from dataclasses import FrozenInstanceError

import pytest

from app.operations_core import (
    OperatorDecisionSelected,
    OperatorSymbolSelected,
    OperatorTimelineSelected,
    OperatorTradeSelected,
)


def test_selection_events_are_frozen_and_slotted() -> None:
    event = OperatorDecisionSelected(
        symbol="AAPL",
        decision_id="decision-1",
    )

    with pytest.raises(FrozenInstanceError):
        event.symbol = "MSFT"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        event.runtime = object()  # type: ignore[attr-defined]


def test_all_selection_events_preserve_broker_neutral_identifiers() -> None:
    assert OperatorSymbolSelected(symbol="AAPL").symbol == "AAPL"
    assert OperatorTradeSelected(symbol="MSFT").symbol == "MSFT"
    assert (
        OperatorDecisionSelected(
            symbol="NVDA",
            decision_id="decision-1",
        ).decision_id
        == "decision-1"
    )
    assert (
        OperatorTimelineSelected(
            symbol="TSLA",
            timeline_entry_id="timeline-1",
        ).timeline_entry_id
        == "timeline-1"
    )


@pytest.mark.parametrize(
    "factory",
    (
        lambda: OperatorSymbolSelected(symbol="aapl"),
        lambda: OperatorTradeSelected(symbol=" AAPL"),
        lambda: OperatorDecisionSelected(
            symbol="AAPL",
            decision_id="",
        ),
        lambda: OperatorTimelineSelected(timeline_entry_id=" "),
        lambda: OperatorSymbolSelected(
            symbol="AAPL",
            selection_source="SERVICE",
        ),
        lambda: OperatorSymbolSelected(
            symbol="AAPL",
            selection_source="ORDER",
        ),
    ),
)
def test_selection_events_reject_invalid_identifiers(factory) -> None:
    with pytest.raises(ValueError):
        factory()

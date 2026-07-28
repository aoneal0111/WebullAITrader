from dataclasses import FrozenInstanceError

import pytest

from app.read_models.operator_workspace import (
    OperatorWorkspaceSnapshot,
    WorkspaceSelectionSource,
)


def test_workspace_selection_sources_are_stable() -> None:
    assert tuple(source.value for source in WorkspaceSelectionSource) == (
        "TIMELINE",
        "DECISION",
        "TRADE",
        "POSITION",
        "ORDER",
        "NONE",
    )


def test_snapshot_is_frozen_slotted_and_initially_empty() -> None:
    snapshot = OperatorWorkspaceSnapshot.initial()

    assert snapshot == OperatorWorkspaceSnapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.selected_symbol = "AAPL"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.runtime = object()  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "changes",
    (
        {"selected_symbol": "aapl"},
        {"selected_trade": "AAPL"},
        {
            "selection_source": WorkspaceSelectionSource.DECISION,
        },
        {
            "selected_symbol": "AAPL",
            "selected_trade": "MSFT",
            "selection_source": WorkspaceSelectionSource.TRADE,
        },
        {
            "selected_symbol": "AAPL",
            "selected_position": "MSFT",
            "selection_source": WorkspaceSelectionSource.POSITION,
        },
        {
            "selected_order": " order-1",
            "selection_source": WorkspaceSelectionSource.ORDER,
        },
        {
            "selected_timeline_entry": "timeline-1",
            "selection_source": WorkspaceSelectionSource.NONE,
        },
        {"selection_source": "TRADE"},
    ),
)
def test_snapshot_rejects_inconsistent_or_invalid_selections(changes) -> None:
    with pytest.raises((TypeError, ValueError)):
        OperatorWorkspaceSnapshot(**changes)


def test_snapshot_accepts_each_specific_selection() -> None:
    assert OperatorWorkspaceSnapshot(
        selected_symbol="AAPL",
        selected_trade="AAPL",
        selection_source=WorkspaceSelectionSource.TRADE,
    ).selected_trade == "AAPL"
    assert OperatorWorkspaceSnapshot(
        selected_symbol="AAPL",
        selected_position="AAPL",
        selection_source=WorkspaceSelectionSource.POSITION,
    ).selected_position == "AAPL"

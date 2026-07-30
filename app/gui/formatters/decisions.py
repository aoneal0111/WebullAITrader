from __future__ import annotations

from app.gui.models.decisions import (
    DecisionDetail,
    DecisionRow,
    DecisionsSnapshot,
)
from app.read_models.decisions import DecisionRecord, DecisionsReadModelSnapshot


def format_decisions(
    snapshot: DecisionsReadModelSnapshot,
    *,
    selected_decision_id: str | None = None,
) -> DecisionsSnapshot:
    if not isinstance(snapshot, DecisionsReadModelSnapshot):
        raise TypeError("snapshot must be a DecisionsReadModelSnapshot")
    rows = tuple(
        DecisionRow(
            decision_id=item.decision_id,
            timestamp=item.timestamp,
            strategy=item.strategy_id,
            symbol=item.symbol,
            action=item.action,
            confidence=f"{item.confidence}%",
            reasoning=item.reasoning_summary,
            risk=item.risk_assessment or "--",
            quantity=item.requested_quantity or "--",
            order_id=item.resulting_order_id or "--",
            outcome=item.execution_outcome.value,
        )
        for item in snapshot.decisions
    )
    selected_record = next(
        (
            item
            for item in snapshot.decisions
            if item.decision_id == selected_decision_id
        ),
        snapshot.decisions[0] if snapshot.decisions else None,
    )
    return DecisionsSnapshot(
        rows=rows,
        selected=(
            _detail(selected_record)
            if selected_record is not None
            else None
        ),
    )


def _detail(record: DecisionRecord) -> DecisionDetail:
    order_id = record.resulting_order_id or "--"
    lifecycle = ["Decision generated"]
    if record.resulting_order_id is not None:
        lifecycle.append(f"Order {record.resulting_order_id}")
    lifecycle.append(record.execution_outcome.value.replace("_", " ").title())
    return DecisionDetail(
        decision_id=record.decision_id,
        title=f"{record.action} {record.symbol}",
        confidence=f"{record.confidence}%",
        reasoning=record.reasoning_summary,
        risk=record.risk_assessment or "--",
        requested_quantity=record.requested_quantity or "--",
        resulting_order_id=order_id,
        lifecycle=tuple(lifecycle),
        execution_outcome=record.execution_outcome.value,
    )

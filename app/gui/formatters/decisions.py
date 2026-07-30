from __future__ import annotations

from app.gui.models.decisions import DecisionRow, DecisionsSnapshot
from app.read_models.decisions import DecisionsReadModelSnapshot


def format_decisions(
    snapshot: DecisionsReadModelSnapshot,
) -> DecisionsSnapshot:
    if not isinstance(snapshot, DecisionsReadModelSnapshot):
        raise TypeError("snapshot must be a DecisionsReadModelSnapshot")
    return DecisionsSnapshot(
        rows=tuple(
            DecisionRow(
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
    )

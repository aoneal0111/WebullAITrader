from __future__ import annotations

from app.operations_core import OperationsDecisionRecord
from app.read_models.decisions.models import (
    DecisionExecutionOutcome,
    DecisionRecord,
    DecisionsReadModelSnapshot,
)


def project_operational_decisions(
    decisions: tuple[OperationsDecisionRecord, ...],
) -> DecisionsReadModelSnapshot:
    return DecisionsReadModelSnapshot(
        decisions=tuple(
            DecisionRecord(
                decision_id=item.decision_id,
                timestamp=item.timestamp,
                strategy_id=item.strategy_id,
                symbol=item.symbol,
                action=item.action,
                confidence=item.confidence,
                reasoning_summary=item.reasoning_summary,
                risk_assessment=item.risk_assessment,
                requested_quantity=item.requested_quantity,
                resulting_order_id=item.resulting_order_id,
                execution_outcome=DecisionExecutionOutcome(
                    item.execution_outcome
                ),
            )
            for item in decisions
        )
    )

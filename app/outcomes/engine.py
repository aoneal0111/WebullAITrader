from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from app.committee.models import thaw_json_value
from app.execution.models import ExecutionStatus
from app.outcomes.models import OutcomeCheck, OutcomeRequest, OutcomeStatus, TradeOutcome
from app.trade_proposals.models import TradeDirection


class OutcomeRecorder:
    name = "outcome_recorder_v1"

    def record(self, request: OutcomeRequest) -> TradeOutcome:
        if not isinstance(request, OutcomeRequest):
            raise ValueError("request must be an OutcomeRequest")
        execution, policy = request.execution_result, request.policy
        filled = execution.status is ExecutionStatus.FILLED
        checks = (OutcomeCheck("execution filled", filled), OutcomeCheck("exit price positive", request.exit_price > 0),
                  OutcomeCheck("quantity positive", execution.filled_quantity > 0))
        if not filled:
            raise ValueError("execution_result must be FILLED")
        quantity = execution.filled_quantity
        gross_cost = execution.fill_price * quantity
        gross_pnl = ((request.exit_price - execution.fill_price) * quantity
                     if execution.direction is TradeDirection.LONG
                     else (execution.fill_price - request.exit_price) * quantity)
        realized_pnl = gross_pnl - execution.commission
        realized_return = realized_pnl / gross_cost
        net_cost = (gross_cost + execution.commission if execution.direction is TradeDirection.LONG
                    else gross_cost - execution.commission)
        outcome_id = _outcome_id(execution.execution_id, request.exit_price, request.timestamp.isoformat(),
                                 policy.version, self.name)
        metadata = dict(thaw_json_value(policy.metadata))
        metadata.update(thaw_json_value(request.metadata))
        metadata.update({"deterministic": True, "pnl": str(realized_pnl), "return": str(realized_return),
                         "engine_version": self.name, "policy_version": policy.version})
        if policy.include_execution_metadata:
            metadata["execution_metadata"] = thaw_json_value(execution.metadata)
        return TradeOutcome(outcome_id, execution.execution_id, execution.proposal_id, execution.symbol,
            execution.direction, quantity, execution.fill_price, request.exit_price, execution.commission,
            execution.slippage, gross_cost, net_cost, realized_pnl, realized_return, request.timestamp,
            OutcomeStatus.CLOSED, policy.version, execution.execution_engine_version,
            execution.proposal_engine_version, execution.risk_policy_version, execution.risk_committee_version,
            self.name, checks if policy.include_checks else (), metadata)


def _outcome_id(execution_id: str, exit_price: Decimal, timestamp: str,
                policy_version: str, engine_version: str) -> str:
    canonical = json.dumps({"execution_id": execution_id, "exit_price": str(exit_price), "timestamp": timestamp,
        "policy_version": policy_version, "engine_version": engine_version}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

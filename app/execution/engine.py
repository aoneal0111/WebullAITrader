from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from app.execution.models import (ExecutionCheck, ExecutionReason, ExecutionResult,
                                  ExecutionStatus, PaperExecutionRequest)
from app.trade_proposals.models import ProposalStatus, TradeDirection


ZERO = Decimal("0")


class PaperExecutionEngine:
    name = "paper_execution_engine_v1"

    def execute(self, request: PaperExecutionRequest) -> ExecutionResult:
        if not isinstance(request, PaperExecutionRequest):
            raise ValueError("request must be a PaperExecutionRequest")
        proposal, policy = request.proposal, request.policy
        ready = proposal.status is ProposalStatus.READY
        quantity_positive = proposal.quantity > ZERO
        entry_positive = proposal.proposed_entry_price > ZERO
        checks = (ExecutionCheck("proposal ready", ready), ExecutionCheck("quantity positive", quantity_positive),
                  ExecutionCheck("entry positive", entry_positive))
        if not ready:
            status, reason = ExecutionStatus.REJECTED, ExecutionReason.PROPOSAL_NOT_READY
        elif not quantity_positive:
            status, reason = ExecutionStatus.REJECTED, ExecutionReason.ZERO_QUANTITY
        elif not entry_positive:
            status, reason = ExecutionStatus.REJECTED, ExecutionReason.INVALID_ENTRY_PRICE
        else:
            status, reason = ExecutionStatus.FILLED, ExecutionReason.FILLED

        filled = proposal.quantity if status is ExecutionStatus.FILLED else ZERO
        if status is ExecutionStatus.FILLED:
            signed_slippage = policy.slippage_per_share if proposal.direction is TradeDirection.LONG else -policy.slippage_per_share
            fill_price = proposal.proposed_entry_price + signed_slippage
            if fill_price <= ZERO:
                status, reason, filled, fill_price = ExecutionStatus.REJECTED, ExecutionReason.INVALID_ENTRY_PRICE, ZERO, ZERO
            commission = max(policy.minimum_commission, policy.commission_per_share * filled) if filled else ZERO
        else:
            fill_price = commission = ZERO
        slippage = policy.slippage_per_share * filled
        gross = fill_price * filled
        net = gross + commission if proposal.direction is TradeDirection.LONG else gross - commission
        execution_id = _execution_id(proposal.proposal_id, request.timestamp.isoformat(), fill_price,
                                     proposal.quantity, policy.version, self.name)
        metadata = {"deterministic": True, "commission": str(commission), "slippage": str(slippage),
                    "gross": str(gross), "net": str(net), "engine_version": self.name,
                    "policy_version": policy.version}
        return ExecutionResult(execution_id, proposal.proposal_id, proposal.symbol, request.timestamp, status, reason,
            proposal.direction, proposal.quantity, filled, proposal.proposed_entry_price, fill_price, commission,
            slippage, gross, net, policy.version, self.name, proposal.proposal_engine_version,
            proposal.risk_policy_version, proposal.risk_committee_version, checks, metadata)


def _execution_id(proposal_id: str, timestamp: str, fill_price: Decimal, quantity: Decimal,
                  policy_version: str, engine_version: str) -> str:
    canonical = json.dumps({"proposal_id": proposal_id, "timestamp": timestamp, "fill_price": str(fill_price),
        "quantity": str(quantity), "policy_version": policy_version, "engine_version": engine_version},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

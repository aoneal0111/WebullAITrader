from __future__ import annotations

from decimal import Decimal

from app.committee.models import CommitteeAction
from app.risk.models import (
    RiskDecision, RiskDecisionAction, RiskEvaluationRequest,
    RiskLimitCheck, RiskReasonCode,
)
from app.risk.policies import RiskPolicy


ZERO = Decimal("0")


class RiskCommittee:
    """Deterministic authorization boundary over normalized supplied state."""

    name = "risk_committee_v1"

    def __init__(self, policy: RiskPolicy | None = None) -> None:
        if policy is not None and not isinstance(policy, RiskPolicy):
            raise ValueError("policy must be a RiskPolicy")
        self._policy = policy or RiskPolicy()

    @property
    def policy(self) -> RiskPolicy:
        return self._policy

    def evaluate(self, request: RiskEvaluationRequest) -> RiskDecision:
        if not isinstance(request, RiskEvaluationRequest):
            raise ValueError("request must be a RiskEvaluationRequest")
        opinion, state, policy = request.committee_opinion, request.risk_state, self._policy
        confidence = Decimal(str(opinion.confidence))
        consensus = Decimal(str(opinion.consensus))
        daily_loss = max(ZERO, -(state.daily_realized_pnl + state.daily_unrealized_pnl) / state.account_equity)
        symbol_limit = state.account_equity * policy.maximum_symbol_exposure_fraction
        gross_limit = state.account_equity * policy.maximum_gross_exposure_fraction
        symbol_capacity = max(ZERO, symbol_limit - state.current_symbol_exposure)
        gross_capacity = max(ZERO, gross_limit - state.total_gross_exposure)

        checks = (
            _check(RiskReasonCode.NEUTRAL_COMMITTEE, opinion.action is not CommitteeAction.NEUTRAL,
                   opinion.action.value, "directional", "Committee action must be directional.", True),
            _check(RiskReasonCode.ZERO_REQUESTED_NOTIONAL, opinion.action is CommitteeAction.NEUTRAL or request.requested_notional > ZERO,
                   request.requested_notional, ZERO, "Requested notional must be greater than zero for directional exposure.", True),
            _check(RiskReasonCode.COMMITTEE_CONFIDENCE_TOO_LOW, confidence >= policy.minimum_committee_confidence,
                   confidence, policy.minimum_committee_confidence, "Committee confidence must meet the configured minimum.", True),
            _check(RiskReasonCode.COMMITTEE_CONSENSUS_TOO_LOW, consensus >= policy.minimum_committee_consensus,
                   consensus, policy.minimum_committee_consensus, "Committee consensus must meet the configured minimum.", True),
            _check(RiskReasonCode.REQUESTED_RISK_LIMIT, request.requested_risk_fraction <= policy.maximum_requested_risk_fraction,
                   request.requested_risk_fraction, policy.maximum_requested_risk_fraction, "Requested risk fraction must not exceed the configured maximum.", False),
            _check(RiskReasonCode.DAILY_LOSS_LIMIT, daily_loss < policy.maximum_daily_loss_fraction,
                   daily_loss, policy.maximum_daily_loss_fraction, "Daily loss fraction must remain below the configured limit.", True),
            _check(RiskReasonCode.DRAWDOWN_LIMIT, state.current_drawdown_fraction < policy.maximum_drawdown_fraction,
                   state.current_drawdown_fraction, policy.maximum_drawdown_fraction, "Drawdown must remain below the configured limit.", True),
            _check(RiskReasonCode.OPEN_POSITION_LIMIT, state.open_positions < policy.maximum_open_positions,
                   state.open_positions, policy.maximum_open_positions, "Open positions must remain below the configured limit.", True),
            _check(RiskReasonCode.OPEN_ORDER_LIMIT, state.open_orders < policy.maximum_open_orders,
                   state.open_orders, policy.maximum_open_orders, "Open orders must remain below the configured limit.", True),
            _capacity_check(RiskReasonCode.INSUFFICIENT_BUYING_POWER, request.requested_notional, state.available_buying_power,
                            "Requested notional exceeded available buying power."),
            _capacity_check(RiskReasonCode.SYMBOL_EXPOSURE_LIMIT, request.requested_notional, symbol_capacity,
                            "Requested notional exceeded remaining symbol exposure capacity."),
            _capacity_check(RiskReasonCode.GROSS_EXPOSURE_LIMIT, request.requested_notional, gross_capacity,
                            "Requested notional exceeded remaining gross exposure capacity."),
        )
        blockers = tuple(check for check in checks if not check.passed and check.blocking)
        reductions = tuple(check for check in checks if not check.passed and not check.blocking)

        approved_risk = min(request.requested_risk_fraction, policy.maximum_requested_risk_fraction)
        risk_notional = request.requested_notional
        if request.requested_risk_fraction > policy.maximum_requested_risk_fraction:
            risk_notional *= approved_risk / request.requested_risk_fraction
        candidate = min(request.requested_notional, state.available_buying_power, symbol_capacity, gross_capacity, risk_notional)

        if blockers or (reductions and not policy.allow_modification) or candidate <= ZERO:
            action = RiskDecisionAction.REJECT
            approved_notional = approved_risk = ZERO
            failed = blockers or reductions
            primary = RiskReasonCode.MULTIPLE_LIMITS if len(failed) > 1 else failed[0].code
            reasons = tuple(_failure_reason(check, policy.allow_modification) for check in failed)
        elif reductions:
            action = RiskDecisionAction.MODIFY
            approved_notional = candidate
            primary = reductions[0].code
            reasons_list = [_reduction_reason(check, request, approved_risk) for check in reductions]
            reasons_list.append(f"Approved notional was reduced from {request.requested_notional} to {candidate}.")
            reasons = tuple(dict.fromkeys(reasons_list))
        else:
            action = RiskDecisionAction.APPROVE
            approved_notional = request.requested_notional
            approved_risk = request.requested_risk_fraction
            primary = RiskReasonCode.APPROVED
            reasons = ("Risk Committee approved the full requested notional.",)

        return RiskDecision(
            symbol=state.symbol, timestamp=request.timestamp, action=action,
            approved_notional=approved_notional, approved_risk_fraction=approved_risk,
            committee_action=opinion.action, committee_confidence=opinion.confidence,
            committee_consensus=opinion.consensus, primary_reason=primary, checks=checks,
            reasons=reasons, policy_version=policy.version, committee_version=self.name,
            metadata={"deterministic": True, "submitted_notional": str(request.requested_notional),
                      "submitted_risk_fraction": str(request.requested_risk_fraction),
                      "approved_notional": str(approved_notional), "approved_risk_fraction": str(approved_risk),
                      "blocking_check_count": len(blockers), "limiting_check_count": len(reductions),
                      "policy_version": policy.version, "risk_committee_version": self.name,
                      "committee_chair_version": opinion.chair_version, "total_checks": len(checks)},
        )


def _check(code: RiskReasonCode, passed: bool, observed: Decimal | int | str,
           limit: Decimal | int | str | None, message: str, blocking: bool) -> RiskLimitCheck:
    return RiskLimitCheck(code, passed, observed, limit, message, blocking)


def _capacity_check(code: RiskReasonCode, requested: Decimal, capacity: Decimal, message: str) -> RiskLimitCheck:
    passed = requested <= capacity
    return _check(code, passed, requested, capacity, message, not passed and capacity <= ZERO)


def _failure_reason(check: RiskLimitCheck, allow_modification: bool) -> str:
    if check.code is RiskReasonCode.NEUTRAL_COMMITTEE:
        return "Committee action was NEUTRAL; no directional exposure was authorized."
    if check.code is RiskReasonCode.ZERO_REQUESTED_NOTIONAL:
        return "Directional committee opinion requested zero notional."
    if check.code is RiskReasonCode.DAILY_LOSS_LIMIT:
        return f"Daily loss fraction {check.observed} met or exceeded the configured limit {check.limit}."
    suffix = " Modification is disabled." if not check.blocking and not allow_modification else ""
    return check.message + suffix


def _reduction_reason(check: RiskLimitCheck, request: RiskEvaluationRequest, approved_risk: Decimal) -> str:
    if check.code is RiskReasonCode.REQUESTED_RISK_LIMIT:
        return f"Requested risk fraction was reduced from {request.requested_risk_fraction} to {approved_risk}."
    return check.message

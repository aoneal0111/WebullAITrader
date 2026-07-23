from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from app.committee.models import CommitteeAction
from app.risk.models import RiskDecisionAction
from app.trade_proposals.models import (
    ProposalReasonCode, ProposalStatus, TradeDirection, TradeProposal,
    TradeProposalCheck, TradeProposalRequest,
)
from app.trade_proposals.policies import ProposalOrderType, TradeProposalPolicy


ZERO = Decimal("0")
ONE = Decimal("1")


class TradeProposalEngine:
    """Create a deterministic planning result without execution coupling."""

    name = "trade_proposal_engine_v1"

    def __init__(self, policy: TradeProposalPolicy | None = None) -> None:
        if policy is not None and not isinstance(policy, TradeProposalPolicy):
            raise ValueError("policy must be a TradeProposalPolicy")
        self._policy = policy

    @property
    def policy(self) -> TradeProposalPolicy | None:
        return self._policy

    def create(self, request: TradeProposalRequest) -> TradeProposal:
        if not isinstance(request, TradeProposalRequest):
            raise ValueError("request must be a TradeProposalRequest")
        policy = self._policy or request.policy
        risk = request.risk_decision
        authorized = risk.action in {RiskDecisionAction.APPROVE, RiskDecisionAction.MODIFY}
        notional_positive = risk.approved_notional > ZERO
        direction = _direction(risk.committee_action)
        calculable = notional_positive and direction is not None

        entry = stop = target = quantity = planned_notional = ZERO
        per_unit_risk = reward_per_unit = ratio = total_risk = expected_reward = ZERO
        if calculable:
            entry = _entry_price(request.reference_price, direction, policy)
            if entry > ZERO:
                stop = _stop_price(entry, direction, policy)
                target = _target_price(entry, direction, policy)
                quantity = _quantity(risk.approved_notional, entry, policy)
                planned_notional = quantity * entry
                if direction is TradeDirection.LONG:
                    per_unit_risk = max(ZERO, entry - stop)
                    reward_per_unit = max(ZERO, target - entry)
                else:
                    per_unit_risk = max(ZERO, stop - entry)
                    reward_per_unit = max(ZERO, entry - target)
                if per_unit_risk > ZERO:
                    ratio = reward_per_unit / per_unit_risk
                total_risk = per_unit_risk * quantity
                expected_reward = reward_per_unit * quantity

        stop_valid = entry > ZERO and stop > ZERO and per_unit_risk > ZERO
        target_valid = entry > ZERO and target > ZERO and reward_per_unit > ZERO
        stop_code = (
            ProposalReasonCode.STOP_DISTANCE_TOO_LARGE
            if entry > ZERO and stop <= ZERO
            else ProposalReasonCode.STOP_DISTANCE_TOO_SMALL
        )
        applicable = calculable and entry > ZERO
        price_increment_valid = applicable and all(
            _is_increment(value, policy.price_increment)
            for value in (entry, stop, target)
        )
        quantity_increment = _effective_quantity_increment(policy)
        checks = (
            _check(ProposalReasonCode.RISK_NOT_APPROVED, authorized, risk.action.value,
                   "APPROVE|MODIFY", "Risk Committee did not authorize directional exposure."),
            _check(ProposalReasonCode.ZERO_APPROVED_NOTIONAL, notional_positive or not authorized,
                   risk.approved_notional, ZERO, "Risk Committee authorized zero notional."),
            _check(ProposalReasonCode.NON_DIRECTIONAL_COMMITTEE, direction is not None or not authorized,
                   risk.committee_action.value, "BULLISH|BEARISH", "Committee action was NEUTRAL; no trade direction was available."),
            _check(ProposalReasonCode.INVALID_REFERENCE_PRICE, request.reference_price > ZERO,
                   request.reference_price, ZERO, "Reference price must be greater than zero."),
            _applicable_check(ProposalReasonCode.INVALID_REFERENCE_PRICE, entry > ZERO, applicable,
                              entry, ZERO, "Calculated entry price must be greater than zero."),
            _applicable_check(stop_code, stop_valid, applicable, per_unit_risk, ZERO,
                              "Calculated stop distance was invalid."),
            _applicable_check(ProposalReasonCode.TARGET_DISTANCE_INVALID, target_valid, applicable,
                              reward_per_unit, ZERO, "Calculated target distance was invalid."),
            _applicable_check(ProposalReasonCode.QUANTITY_BELOW_MINIMUM, quantity >= policy.minimum_quantity,
                              applicable, quantity, policy.minimum_quantity,
                              "Calculated quantity was below the configured minimum."),
            _applicable_check(ProposalReasonCode.QUANTITY_INCREMENT_INVALID,
                              _is_increment(quantity, quantity_increment), applicable,
                              quantity, quantity_increment, "Calculated quantity did not match the required increment."),
            _applicable_check(ProposalReasonCode.PRICE_INCREMENT_INVALID, price_increment_valid,
                              applicable, entry, policy.price_increment,
                              "Calculated prices did not match the required increment."),
            _applicable_check(ProposalReasonCode.NOTIONAL_BELOW_MINIMUM,
                              planned_notional >= policy.minimum_notional, applicable,
                              planned_notional, policy.minimum_notional,
                              "Calculated planned notional was below the configured minimum."),
            _applicable_check(ProposalReasonCode.RISK_REWARD_TOO_LOW,
                              ratio >= policy.minimum_risk_reward_ratio, applicable,
                              ratio, policy.minimum_risk_reward_ratio,
                              "Risk/reward ratio was below the configured minimum."),
            _maximum_check(quantity, policy.maximum_quantity, applicable),
        )
        failed = tuple(check for check in checks if not check.passed and check.blocking)
        ready = not failed and planned_notional <= risk.approved_notional
        if ready:
            status = ProposalStatus.READY
            primary = ProposalReasonCode.READY
            reasons = ("Trade proposal is ready for later compliance evaluation.",)
            output_notional, output_quantity = risk.approved_notional, quantity
            output_total_risk, output_reward = total_risk, expected_reward
        else:
            status = ProposalStatus.REJECTED
            primary = ProposalReasonCode.MULTIPLE_CONSTRAINTS if len(failed) > 1 else failed[0].code
            reasons = tuple(check.message for check in failed)
            output_notional = output_quantity = output_total_risk = output_reward = ZERO

        proposal_id = _proposal_id(request, policy, self.name)
        return TradeProposal(
            proposal_id=proposal_id, symbol=risk.symbol, timestamp=request.timestamp,
            status=status, direction=direction, order_type=policy.order_type,
            approved_notional=output_notional, quantity=output_quantity,
            reference_price=request.reference_price, proposed_entry_price=entry,
            stop_loss_price=stop, take_profit_price=target, per_unit_risk=per_unit_risk,
            total_planned_risk=output_total_risk, expected_reward=output_reward,
            risk_reward_ratio=ratio, primary_reason=primary, checks=checks, reasons=reasons,
            policy_version=policy.version, proposal_engine_version=self.name,
            risk_policy_version=risk.policy_version, risk_committee_version=risk.committee_version,
            metadata={"deterministic": True, "authorized_notional": str(risk.approved_notional),
                      "planned_notional": str(planned_notional),
                      "unused_authorized_notional": str(max(ZERO, risk.approved_notional - planned_notional)),
                      "reference_price": str(request.reference_price), "entry_price": str(entry),
                      "quantity_increment": str(quantity_increment), "price_increment": str(policy.price_increment),
                      "fractional_quantity_enabled": policy.allow_fractional_quantity,
                      "risk_decision_action": risk.action.value, "risk_primary_reason": risk.primary_reason.value,
                      "risk_policy_version": risk.policy_version, "risk_committee_version": risk.committee_version,
                      "proposal_policy_version": policy.version, "proposal_engine_version": self.name,
                      "total_checks": len(checks), "failed_check_count": len(failed)},
        )


def _direction(action: CommitteeAction) -> TradeDirection | None:
    if action is CommitteeAction.BULLISH: return TradeDirection.LONG
    if action is CommitteeAction.BEARISH: return TradeDirection.SHORT
    return None


def _entry_price(reference: Decimal, direction: TradeDirection, policy: TradeProposalPolicy) -> Decimal:
    if policy.order_type is ProposalOrderType.MARKET:
        return reference
    factor = ONE - policy.limit_price_offset_fraction if direction is TradeDirection.LONG else ONE + policy.limit_price_offset_fraction
    rounding = ROUND_FLOOR if direction is TradeDirection.LONG else ROUND_CEILING
    return _round_increment(reference * factor, policy.price_increment, rounding)


def _stop_price(entry: Decimal, direction: TradeDirection, policy: TradeProposalPolicy) -> Decimal:
    factor = ONE - policy.stop_loss_fraction if direction is TradeDirection.LONG else ONE + policy.stop_loss_fraction
    rounding = ROUND_FLOOR if direction is TradeDirection.LONG else ROUND_CEILING
    return max(ZERO, _round_increment(entry * factor, policy.price_increment, rounding))


def _target_price(entry: Decimal, direction: TradeDirection, policy: TradeProposalPolicy) -> Decimal:
    factor = ONE + policy.take_profit_fraction if direction is TradeDirection.LONG else ONE - policy.take_profit_fraction
    rounding = ROUND_CEILING if direction is TradeDirection.LONG else ROUND_FLOOR
    return max(ZERO, _round_increment(entry * factor, policy.price_increment, rounding))


def _quantity(notional: Decimal, entry: Decimal, policy: TradeProposalPolicy) -> Decimal:
    increment = _effective_quantity_increment(policy)
    quantity = _round_increment(notional / entry, increment, ROUND_FLOOR)
    if policy.maximum_quantity is not None:
        maximum = _round_increment(policy.maximum_quantity, increment, ROUND_FLOOR)
        quantity = min(quantity, maximum)
    return quantity


def _effective_quantity_increment(policy: TradeProposalPolicy) -> Decimal:
    return policy.quantity_increment if policy.allow_fractional_quantity else max(ONE, policy.quantity_increment)


def _round_increment(value: Decimal, increment: Decimal, rounding: str) -> Decimal:
    return (value / increment).to_integral_value(rounding=rounding) * increment


def _is_increment(value: Decimal, increment: Decimal) -> bool:
    return value >= ZERO and value % increment == ZERO


def _check(code: ProposalReasonCode, passed: bool, observed: Decimal | str,
           limit: Decimal | str | None, message: str) -> TradeProposalCheck:
    return TradeProposalCheck(code, passed, observed, limit, message, True)


def _applicable_check(code: ProposalReasonCode, passed: bool, applicable: bool,
                      observed: Decimal, limit: Decimal, message: str) -> TradeProposalCheck:
    if not applicable:
        return TradeProposalCheck(code, True, ZERO, limit, "Check was not applicable to the rejected authorization.", True)
    return _check(code, passed, observed, limit, message)


def _maximum_check(quantity: Decimal, maximum: Decimal | None, applicable: bool) -> TradeProposalCheck:
    if maximum is None:
        return TradeProposalCheck(ProposalReasonCode.QUANTITY_INCREMENT_INVALID, True, "not configured", None,
                                  "Maximum quantity was not configured.", True)
    return _applicable_check(ProposalReasonCode.QUANTITY_INCREMENT_INVALID, quantity <= maximum,
                             applicable, quantity, maximum, "Calculated quantity exceeded the configured maximum.")


def _proposal_id(request: TradeProposalRequest, policy: TradeProposalPolicy, engine_version: str) -> str:
    risk = request.risk_decision
    canonical = json.dumps({"symbol": risk.symbol, "timestamp": request.timestamp.isoformat(),
        "risk_timestamp": risk.timestamp.isoformat(), "risk_action": risk.action.value,
        "approved_notional": str(risk.approved_notional), "reference_price": str(request.reference_price),
        "policy_version": policy.version, "engine_version": engine_version}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

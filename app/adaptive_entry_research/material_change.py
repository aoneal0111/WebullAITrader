"""Explainable event-scale admission for working-entry reassessment."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .contracts import MaterialChangeReason, WorkingEntrySnapshot


@dataclass(frozen=True, slots=True)
class MaterialChangePolicy:
    price_displacement_r: Decimal = Decimal("0.25")
    quote_displacement_r: Decimal = Decimal("0.20")
    spread_change_ratio: Decimal = Decimal("0.50")
    nearing_expiry_seconds: Decimal = Decimal("10")


def detect_material_change(
    previous: WorkingEntrySnapshot | None,
    current: WorkingEntrySnapshot,
    *,
    policy: MaterialChangePolicy = MaterialChangePolicy(),
) -> tuple[MaterialChangeReason, ...]:
    reasons: list[MaterialChangeReason] = []
    risk = current.original_risk_per_share
    current_price = current.last or current.ask or current.bid
    if previous is None:
        baseline = current.original_limit_price
        if current_price is not None and abs(current_price - baseline) / risk >= policy.price_displacement_r:
            reasons.append(MaterialChangeReason.PRICE_DISPLACEMENT)
    else:
        previous_price = previous.last or previous.ask or previous.bid
        if current_price is not None and previous_price is not None and abs(current_price - previous_price) / risk >= policy.price_displacement_r:
            reasons.append(MaterialChangeReason.PRICE_DISPLACEMENT)
        if current.bid is not None and current.ask is not None and previous.bid is not None and previous.ask is not None:
            if max(abs(current.bid - previous.bid), abs(current.ask - previous.ask)) / risk >= policy.quote_displacement_r:
                reasons.append(MaterialChangeReason.QUOTE_DISPLACEMENT)
        if current.spread is not None and previous.spread is not None:
            denominator = max(previous.spread, Decimal("0.000001"))
            if abs(current.spread - previous.spread) / denominator >= policy.spread_change_ratio:
                reasons.append(MaterialChangeReason.SPREAD_CHANGE)
        if current.momentum_velocity != previous.momentum_velocity and current.momentum_velocity is not None:
            reasons.append(MaterialChangeReason.MOMENTUM_ACCELERATION)
        if current.volume_acceleration != previous.volume_acceleration and current.volume_acceleration is not None:
            reasons.append(MaterialChangeReason.VOLUME_ACCELERATION)
        if current.current_reference_price != previous.current_reference_price:
            reasons.append(MaterialChangeReason.REFERENCE_PRICE_CHANGED)
        if current.current_structural_stop != previous.current_structural_stop:
            reasons.append(MaterialChangeReason.STRUCTURAL_STOP_CHANGED)
        if current.setup_state != previous.setup_state:
            reasons.append(MaterialChangeReason.SETUP_STATE_CHANGED)
        if current.current_technical_actionable != previous.current_technical_actionable:
            reasons.append(MaterialChangeReason.TECHNICAL_ACTIONABILITY_CHANGED)
        if current.terminal_reason and current.terminal_reason != previous.terminal_reason:
            reasons.append(MaterialChangeReason.ORDER_TERMINATED)
    if current.remaining_validity_seconds <= policy.nearing_expiry_seconds and (
        previous is None or previous.remaining_validity_seconds > policy.nearing_expiry_seconds
    ):
        reasons.append(MaterialChangeReason.ORDER_NEARING_EXPIRY)
    return tuple(dict.fromkeys(reasons))


def semantic_signature(snapshot: WorkingEntrySnapshot, reasons: tuple[MaterialChangeReason, ...]) -> tuple[object, ...]:
    """Return an episode-scale key, excluding quote-by-quote noise.

    The key deliberately retains state boundaries which can change the
    evaluator's decision, while omitting the raw spread value.  Spread is
    represented only by the evaluator's existing wide/acceptable gate.  This
    is persistence semantics, not a trading threshold.
    """
    risk = snapshot.original_risk_per_share
    bucket = lambda value: None if value is None else int((value / risk * 4).to_integral_value())
    market = snapshot.last or snapshot.ask or snapshot.bid
    drift_r = None if market is None else (market - snapshot.original_limit_price) / risk
    if drift_r is None:
        displacement = "MISSING"
    else:
        magnitude = abs(drift_r)
        if magnitude < Decimal("0.25"):
            band = "KEEP"
        elif magnitude < Decimal("0.75"):
            band = "RETRACE"
        elif magnitude < Decimal("1.50"):
            band = "DISPLACED"
        elif magnitude < Decimal("3"):
            band = "REPRICE"
        else:
            band = "ABANDON"
        displacement = ("-" if drift_r < 0 else "+", band)
    if snapshot.spread_percent is None:
        spread_gate = "MISSING"
    elif snapshot.spread_percent > Decimal("2"):
        spread_gate = "WIDE"
    else:
        spread_gate = "ACCEPTABLE"
    important_reasons = tuple(sorted(
        reason.value for reason in reasons
        if reason is not MaterialChangeReason.SPREAD_CHANGE
        and reason is not MaterialChangeReason.PRICE_DISPLACEMENT
    ))
    return (
        snapshot.order_id, snapshot.order_status, snapshot.remaining_quantity,
        snapshot.filled_quantity, snapshot.setup_type, snapshot.setup_state,
        snapshot.warrior_current_state, snapshot.current_technical_actionable,
        bucket(snapshot.current_setup_quality),
        bucket(snapshot.current_reference_price), bucket(snapshot.current_structural_stop),
        tuple(snapshot.unavailable_evidence), snapshot.quote_freshness_seconds is None,
        snapshot.quote_freshness_seconds is not None and snapshot.quote_freshness_seconds > Decimal("5"),
        spread_gate, displacement, important_reasons, snapshot.terminal_reason,
    )


__all__ = ["MaterialChangePolicy", "detect_material_change", "semantic_signature"]

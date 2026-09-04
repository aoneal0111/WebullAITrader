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
    """Exclude timestamps and raw event identities from semantic deduplication."""
    risk = snapshot.original_risk_per_share
    bucket = lambda value: None if value is None else int((value / risk * 4).to_integral_value())
    return (
        snapshot.order_status, snapshot.remaining_quantity, snapshot.filled_quantity,
        bucket(snapshot.bid), bucket(snapshot.ask), bucket(snapshot.last),
        bucket(snapshot.spread), snapshot.setup_state,
        bucket(snapshot.current_reference_price), bucket(snapshot.current_structural_stop),
        snapshot.current_technical_actionable,
        tuple(reason.value for reason in reasons), snapshot.terminal_reason,
    )


__all__ = ["MaterialChangePolicy", "detect_material_change", "semantic_signature"]

"""Bounded, structural context for evaluating an entry relative to its trigger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class EntryExtensionAssessment:
    classification: str
    trigger_extension_percent: Decimal | None
    risk_normalized_extension: Decimal | None
    hod_distance_percent: Decimal | None
    at_hod: bool | None
    vwap_extension_percent: Decimal | None
    move_since_observation_percent: Decimal | None
    move_since_forming_percent: Decimal | None
    move_since_trigger_percent: Decimal | None
    seconds_since_trigger: Decimal | None


def assess_entry_extension(
    *,
    entry_price: Decimal,
    trigger_price: Decimal,
    structural_stop: Decimal,
    first_observation_price: Decimal | None = None,
    forming_price: Decimal | None = None,
    hod: Decimal | None = None,
    vwap: Decimal | None = None,
    trigger_at: datetime | None = None,
    evaluated_at: datetime | None = None,
    displacement_limit: Decimal | None = None,
    continuation_confirmed: bool = False,
) -> EntryExtensionAssessment:
    """Compute constant-time, diagnostic-only extension context.

    The only hard classification boundary supplied here is the existing
    adaptive displacement envelope.  No new percentage or freshness policy is
    introduced by this helper.
    """
    if any(value <= ZERO or not value.is_finite() for value in
           (entry_price, trigger_price, structural_stop)) or structural_stop >= trigger_price:
        return EntryExtensionAssessment("UNKNOWN", None, None, None, None, None, None, None, None, None)
    trigger_extension = (entry_price - trigger_price) / trigger_price * HUNDRED
    risk = trigger_price - structural_stop
    risk_extension = ((entry_price - trigger_price) / risk
                      if risk > ZERO else None)
    hod_distance = ((hod - entry_price) / hod * HUNDRED
                    if hod is not None and hod > ZERO else None)
    at_hod = (hod is not None and entry_price >= hod) if hod is not None else None
    vwap_extension = ((entry_price - vwap) / vwap * HUNDRED
                      if vwap is not None and vwap > ZERO else None)

    def move_from(start: Decimal | None) -> Decimal | None:
        return ((entry_price - start) / start * HUNDRED
                if start is not None and start > ZERO else None)

    seconds = (Decimal(str((evaluated_at - trigger_at).total_seconds()))
               if trigger_at is not None and evaluated_at is not None else None)
    if entry_price <= trigger_price or (
        displacement_limit is not None and entry_price <= displacement_limit
    ):
        classification = "NEAR_TRIGGER"
    elif continuation_confirmed:
        classification = "VALID_CONTINUATION"
    elif seconds is not None and seconds < ZERO:
        classification = "UNKNOWN"
    else:
        classification = "EXTENDED"
    return EntryExtensionAssessment(
        classification, trigger_extension, risk_extension, hod_distance, at_hod,
        vwap_extension, move_from(first_observation_price),
        move_from(forming_price), move_from(trigger_price), seconds,
    )

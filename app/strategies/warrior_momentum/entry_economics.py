"""Execution must not manufacture new upside when paying a higher entry."""
from decimal import Decimal


def remaining_reward_ok(*, entry, stop, targets, spread, minimum_first_r=Decimal('0.5'),
                        minimum_final_r=Decimal('1.5')):
    """Conservative quote-cost allowance; targets are plans, not forecasts.

    One quoted spread is reserved for execution friction. This does not model
    commissions, gaps, or prove that any target is reachable.
    """
    values = (entry, stop, spread, minimum_first_r, minimum_final_r, *targets)
    if len(targets) < 2 or any(not v.is_finite() for v in values):
        return False
    if stop <= 0 or entry <= stop or spread < 0:
        return False
    if minimum_first_r <= 0 or minimum_final_r <= 0:
        return False
    if any(b <= a for a, b in zip(targets, targets[1:])):
        return False
    risk = entry - stop + spread
    return (targets[0] - entry - spread >= minimum_first_r * risk
            and targets[-1] - entry - spread >= minimum_final_r * risk)

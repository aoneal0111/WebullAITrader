"""Keep a scalp's entry authority inside its original liquidity session."""
from datetime import datetime, time, timedelta

from app.market.calendar import EASTERN, trading_day_schedule


def scalp_entry_window(at: datetime, holding_seconds: int = 300) -> tuple[str, datetime] | None:
    """Return (session, last entry time), leaving the full holding budget.

    Session changes are execution-regime changes, including early closes.
    This is entry admission only; it never cancels protection for filled shares.
    """
    if at.utcoffset() is None or holding_seconds <= 0:
        raise ValueError("aware time and positive holding budget required")
    local = at.astimezone(EASTERN)
    schedule = trading_day_schedule(local)
    if schedule is None:
        return None
    start = datetime.combine(local.date(), time(4), EASTERN)
    end = datetime.combine(local.date(), time(20), EASTERN)
    if not start <= local < end:
        return None
    if local < schedule.market_open:
        session, boundary = "PREMARKET", schedule.market_open
    elif local < schedule.market_close:
        session, boundary = "REGULAR", schedule.market_close
    else:
        session, boundary = "AFTER_HOURS", end
    return session, boundary - timedelta(seconds=holding_seconds)


def scalp_entry_deadline(at: datetime, metadata) -> datetime:
    """Bound recovered/review-delayed authority by its original deadline."""
    try:
        holding = int(metadata.get("scalp_maximum_holding_seconds", 300))
        window = scalp_entry_window(at, holding)
        deadline = window[1] if window else at
        original = metadata.get("scalp_entry_session_deadline")
        if original:
            original = datetime.fromisoformat(str(original))
            if original.utcoffset() is None:
                return at
            deadline = min(deadline, original)
        return deadline
    except (ValueError, TypeError, OverflowError):
        # Invalid recovered entry metadata expires entry authority rather than
        # interrupting reconciliation of unrelated filled-share protection.
        return at

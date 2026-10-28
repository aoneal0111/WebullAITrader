"""Shared offline exit mechanics, extracted unchanged from knowledge.exit_replay."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from typing import Iterable, Protocol
D = Decimal
MINUTE = timedelta(minutes=1)

class HistoricalBar(Protocol):
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

@dataclass(frozen=True)
class ExitPolicy:
    name: str
    target_r: Decimal = D("1")
    partial_fraction: Decimal = D("0")
    break_even_after_partial: bool = False
    trail_distance_r: Decimal | None = None

    def __post_init__(self):
        values = (self.target_r, self.partial_fraction)
        if not self.name or any(not v.is_finite() for v in values):
            raise ValueError("INVALID_POLICY")
        if self.target_r <= 0 or not D(0) <= self.partial_fraction <= D(1):
            raise ValueError("INVALID_POLICY")
        if self.trail_distance_r is not None and (
            not self.trail_distance_r.is_finite() or self.trail_distance_r <= 0
        ):
            raise ValueError("INVALID_TRAIL")


@dataclass(frozen=True)
class ExitFill:
    timestamp: datetime
    quantity: Decimal
    price: Decimal
    reason: str
    timing: str


@dataclass(frozen=True)
class ReplayResult:
    policy: str
    status: str
    remaining: Decimal
    net_pnl: Decimal | None
    cost_paid: Decimal
    fills: tuple[ExitFill, ...]
    last_stop: Decimal


POLICIES = (
    ExitPolicy("STRUCTURAL_STOP_AND_TIME"),
    ExitPolicy("HALF_AT_1R_THEN_BREAK_EVEN", partial_fraction=D("0.5"),
               break_even_after_partial=True),
    ExitPolicy("HALF_AT_1R_THEN_1R_TRAIL", partial_fraction=D("0.5"),
               break_even_after_partial=True, trail_distance_r=D("1")),
)

# Compare activation thresholds at the same partial size and runner policy.
# These are bar-mechanics proxies, not Warrior's full profit-harvest engine.
EARLY_PARTIAL_POLICIES = (
    ExitPolicy("QUARTER_AT_075R_THEN_BREAK_EVEN", target_r=D("0.75"),
               partial_fraction=D("0.25"), break_even_after_partial=True),
    ExitPolicy("QUARTER_AT_050R_THEN_BREAK_EVEN", target_r=D("0.50"),
               partial_fraction=D("0.25"), break_even_after_partial=True),
)
POLICY_SETS = {"BASELINE": POLICIES,
               "EARLY_PARTIAL_V1": POLICIES + EARLY_PARTIAL_POLICIES}


def size_entry(entry_price, initial_stop, risk_dollars, cost_per_side,
               mode="PRICE_RISK", capital_cap=None):
    """Declared planned-stop risk, not a guarantee against gaps or liquidity."""
    values = (entry_price, initial_stop, risk_dollars, cost_per_side)
    if (any(not v.is_finite() for v in values) or
            not entry_price > initial_stop > 0 or risk_dollars <= 0 or cost_per_side < 0):
        raise ValueError("INVALID_SIZING_INPUT")
    if mode not in ("PRICE_RISK", "COST_INCLUSIVE"):
        raise ValueError("INVALID_SIZING_MODE")
    if capital_cap is not None and (not capital_cap.is_finite() or capital_cap <= 0):
        raise ValueError("INVALID_CAPITAL_CAP")
    if mode == "COST_INCLUSIVE" and capital_cap is None:
        raise ValueError("COST_INCLUSIVE_REQUIRES_CAPITAL_CAP")
    price_risk = entry_price - initial_stop
    risk_per_share = price_risk + (2 * cost_per_side if mode == "COST_INCLUSIVE" else D(0))
    risk_qty = (risk_dollars / risk_per_share).to_integral_value(rounding=ROUND_FLOOR)
    qty = risk_qty
    capital_qty = None
    if capital_cap is not None:
        # Reserve entry cost as well as purchase notional inside this cap.
        capital_qty = (capital_cap / (entry_price + cost_per_side)).to_integral_value(rounding=ROUND_FLOOR)
        qty = min(qty, capital_qty)
    if mode == "COST_INCLUSIVE":
        qty = min(qty, D(10000))
    return {"mode": mode, "quantity": qty, "risk_limited_quantity": risk_qty,
            "capital_limited_quantity": capital_qty, "capital_cap": capital_cap,
            "purchase_notional": qty * entry_price,
            "entry_cash_required": qty * (entry_price + cost_per_side),
            "planned_price_risk": qty * price_risk,
            "estimated_round_trip_cost": qty * 2 * cost_per_side,
            "planned_stop_loss_with_cost": qty * (price_risk + 2 * cost_per_side)}


def next_minute_entry(bars, *, symbol, detected_time, planned_price, initial_stop):
    """Use only the exact next minute's open, never a later available bar.

    An OHLC open is a research proxy, not a Webull quote or executable fill.
    Replacing the planned entry is a distinct experiment, not a limit fill.
    """
    if detected_time.tzinfo is None or detected_time.utcoffset() is None:
        raise ValueError("INVALID_ENTRY_TIME")
    bars = tuple(bars)
    previous = None
    for bar in bars:
        if bar.symbol != symbol or (previous is not None and bar.timestamp <= previous):
            raise ValueError("MIXED_SYMBOL_OR_UNORDERED_BARS")
        previous = bar.timestamp
    entry_time = detected_time.replace(second=0, microsecond=0) + MINUTE
    audit = {"model": "NEXT_MINUTE_OPEN", "detected_time": detected_time,
             "planned_entry_price": planned_price, "entry_time": entry_time,
             "entry_price": None, "open_minus_planned": None}
    candidate = next((b for b in bars if b.timestamp == entry_time), None)
    if candidate is None:
        audit["status"] = "NO_ENTRY_MISSING_NEXT_MINUTE"
    elif candidate.open <= initial_stop:
        audit["status"] = "NO_ENTRY_OPEN_AT_OR_BELOW_STOP"
        audit["observed_open"] = candidate.open
    else:
        audit.update(status="ENTRY_OPEN_PROXY", entry_price=candidate.open,
                     open_minus_planned=candidate.open - planned_price)
    return audit


def replay_long(bars: Iterable[HistoricalBar], *, symbol: str,
                entry_time: datetime, entry_price: Decimal, initial_stop: Decimal,
                quantity: Decimal, policy: ExitPolicy, hold_minutes: int = 60,
                cost_per_share_per_side: Decimal = D("0")) -> ReplayResult:
    """Hold the supplied entry fixed; new stop levels take effect next bar.

    The entry is assumed filled immediately before a bar at entry_time. A bar
    overlapping the entry, a missing minute, or stop/target conflict has no
    resolvable path. A hold deadline exits at the next contiguous bar's open.
    Integer shares are required and partial quantities round down.
    """
    values = (entry_price, initial_stop, quantity, cost_per_share_per_side)
    if entry_time.tzinfo is None or not symbol or hold_minutes <= 0:
        raise ValueError("INVALID_ENTRY")
    if any(not v.is_finite() for v in values) or not entry_price > initial_stop > 0:
        raise ValueError("INVALID_GEOMETRY")
    if quantity <= 0 or quantity != quantity.to_integral_value() or cost_per_share_per_side < 0:
        raise ValueError("INVALID_SIZE_OR_COST")
    remaining, stop = quantity, initial_stop
    risk = entry_price - initial_stop
    target = entry_price + policy.target_r * risk
    partial_size = (quantity * policy.partial_fraction).to_integral_value(rounding=ROUND_FLOOR)
    took_partial = False
    fills: list[ExitFill] = []
    costs = quantity * cost_per_share_per_side
    pnl = -costs
    expected = entry_time
    previous = None
    deadline = entry_time + timedelta(minutes=hold_minutes)
    seen = False

    def result(status):
        return ReplayResult(policy.name, status, remaining,
                            pnl if remaining == 0 else None, costs, tuple(fills), stop)

    def sell(qty, price, bar, reason, timing):
        nonlocal remaining, costs, pnl
        fee = qty * cost_per_share_per_side
        costs += fee
        pnl += qty * (price - entry_price) - fee
        remaining -= qty
        fills.append(ExitFill(bar.timestamp, qty, price, reason, timing))

    for bar in bars:
        if bar.symbol != symbol or (previous is not None and bar.timestamp <= previous):
            raise ValueError("MIXED_SYMBOL_OR_UNORDERED_BARS")
        previous = bar.timestamp
        if bar.timestamp < entry_time:
            if bar.timestamp + MINUTE > entry_time:
                return result("UNRESOLVED_ENTRY_BAR")
            continue
        if bar.timestamp != expected:
            return result("UNRESOLVED_MISSING_BARS")
        seen = True
        expected = bar.timestamp + MINUTE
        if bar.open <= stop:
            sell(remaining, bar.open, bar, "STOP_GAP_OR_OPEN", "BAR_OPEN")
            return result("CLOSED")
        if bar.timestamp >= deadline:
            sell(remaining, bar.open, bar, "MAX_HOLD", "BAR_OPEN")
            return result("CLOSED")

        target_pending = partial_size > 0 and not took_partial
        # An opening price above a sell target establishes that event first.
        target_at_open = target_pending and bar.open >= target
        stop_touched = bar.low <= stop
        target_touched = target_pending and bar.high >= target
        if stop_touched and target_touched and not target_at_open:
            return result("UNRESOLVED_INTRABAR_ORDER")
        if target_at_open or (target_touched and not stop_touched):
            sell(partial_size, target, bar, "PARTIAL_TARGET",
                 "BAR_OPEN" if target_at_open else "INTRABAR_TIME_UNKNOWN")
            took_partial = True
            if remaining == 0:
                return result("CLOSED")
        if stop_touched:
            sell(remaining, stop, bar, "STOP", "INTRABAR_TIME_UNKNOWN")
            return result("CLOSED")
        # Never apply the completed bar's high retroactively to its own low.
        if took_partial and policy.break_even_after_partial:
            stop = max(stop, entry_price)
        if took_partial and policy.trail_distance_r is not None:
            stop = max(stop, bar.high - policy.trail_distance_r * risk)
    return result("OPEN_AT_DATA_END" if seen else "NO_POST_ENTRY_BARS")


def compare(bars: Iterable[HistoricalBar], *, policies=POLICIES, **entry):
    path = tuple(bars)
    return tuple(replay_long(path, policy=policy, **entry) for policy in policies)

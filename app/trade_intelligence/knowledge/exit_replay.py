"""Bounded, offline long-exit comparisons on ordered one-minute OHLC bars.

Prices are bar proxies, not NBBO or actual fills. Missing bars and unknown
intrabar order remain unresolved rather than receiving future peak proceeds.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_FLOOR
from itertools import islice
import hashlib
import json
from pathlib import Path
from typing import Iterable

from .mining import JsonlBarProvider
from .models import HistoricalBar

D = Decimal
MINUTE = timedelta(minutes=1)


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


def compare(bars: Iterable[HistoricalBar], **entry):
    path = tuple(bars)
    return tuple(replay_long(path, policy=policy, **entry) for policy in POLICIES)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--max-scan", type=int, default=100)
    parser.add_argument("--max-episodes", type=int, default=10)
    parser.add_argument("--strategy", default="HIGH_OF_DAY_BREAKOUT")
    parser.add_argument("--hold-minutes", type=int, default=60)
    parser.add_argument("--risk-dollars", type=D, default=D("25"))
    parser.add_argument("--cost-per-share-per-side", type=D, default=D("0.01"))
    args = parser.parse_args(argv)
    if not 0 < args.max_scan <= 1000 or not 0 < args.max_episodes <= 100:
        parser.error("Use at most 1000 scanned rows and 100 episodes per batch")
    if not args.risk_dollars.is_finite() or args.risk_dollars <= 0:
        parser.error("risk-dollars must be positive and finite")
    if args.hold_minutes <= 0 or not args.cost_per_share_per_side.is_finite() or args.cost_per_share_per_side < 0:
        parser.error("hold-minutes must be positive; costs must be finite and nonnegative")
    selected = scanned = paired = 0
    totals = {p.name: D(0) for p in POLICIES}
    print("EVIDENCE: MINUTE_BAR_PROXY_FIXED_PLANNED_ENTRY_NOT_ACTUAL_FILLS")
    print("PORTFOLIO: INDEPENDENT_EPISODES_NO_SHARED_CAPITAL_OR_OVERLAP_MODEL")
    with (args.root / "corpus_repaired/episodes.jsonl").open(encoding="utf-8") as handle:
        for line in islice(handle, args.max_scan):
            scanned += 1
            row = json.loads(line)
            if args.strategy not in row.get("strategy_memberships", ()):
                continue
            symbol = row["symbol"]
            path = args.root / "normalized" / f"{symbol}_{row['trading_date']}.jsonl"
            entry_price, stop = D(str(row["trigger_price"])), D(str(row["structural_stop"]))
            if not entry_price > stop > 0 or not path.is_file():
                continue
            qty = (args.risk_dollars / (entry_price - stop)).to_integral_value(rounding=ROUND_FLOOR)
            if not 0 < qty <= 10000:
                continue
            # Bound source lines too: the provider quarantines malformed rows,
            # so bounding only its yielded bars would not bound the read.
            with path.open(encoding="utf-8") as source:
                for count, source_line in enumerate(islice(source, 5001), 1):
                    if count > 5000 or len(source_line) > 1_000_000:
                        raise ValueError("OVERSIZED_BAR_PARTITION")
            provider = JsonlBarProvider(path)
            bars = tuple(islice(provider.bars(), 5001))
            if provider.errors or len(bars) > 5000:
                raise ValueError("INVALID_OR_OVERSIZED_BAR_PARTITION")
            results = compare(bars, symbol=symbol,
                entry_time=datetime.fromisoformat(row["detected_timestamp"]),
                entry_price=entry_price, initial_stop=stop, quantity=qty,
                hold_minutes=args.hold_minutes,
                cost_per_share_per_side=args.cost_per_share_per_side)
            selected += 1
            print(json.dumps({"episode_id": row["episode_id"], "symbol": symbol,
                "normalized_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "policy_version": "CHRONOLOGICAL_BAR_EXITS_V1",
                "hold_minutes": args.hold_minutes,
                "cost_per_share_per_side": args.cost_per_share_per_side,
                "entry_time": row["detected_timestamp"], "entry_price": entry_price,
                "initial_stop": stop, "quantity": qty,
                "results": [asdict(r) for r in results]}, default=str))
            if all(r.status == "CLOSED" for r in results):
                paired += 1
                for r in results:
                    totals[r.policy] += r.net_pnl
            if selected >= args.max_episodes:
                break
    print(json.dumps({"scanned_rows": scanned, "selected_episodes": selected,
        "paired_closed_episodes": paired, "excluded_from_paired_totals": selected - paired,
        "paired_pnl_totals": totals,
        "selection": "BOUNDED_FILE_ORDER_SAMPLE_NOT_REPRESENTATIVE_PERFORMANCE"}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

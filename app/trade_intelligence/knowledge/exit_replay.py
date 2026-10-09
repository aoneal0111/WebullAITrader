"""Bounded, offline long-exit comparisons on ordered one-minute OHLC bars.

Prices are bar proxies, not NBBO or actual fills. Missing bars and unknown
intrabar order remain unresolved rather than receiving future peak proceeds.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from collections import Counter
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


def coverage_bucket(policies=POLICIES):
    return {"selected_episodes": 0, "paired_closed_episodes": 0,
            "status_counts": {p.name: Counter() for p in policies},
            "paired_pnl_totals": {p.name: D(0) for p in policies}}


def record_coverage(bucket, results):
    bucket["selected_episodes"] += 1
    for result in results:
        bucket["status_counts"][result.policy][result.status] += 1
    if all(result.status == "CLOSED" for result in results):
        bucket["paired_closed_episodes"] += 1
        for result in results:
            bucket["paired_pnl_totals"][result.policy] += result.net_pnl


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, default=str, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--max-scan", type=int, default=100)
    parser.add_argument("--max-episodes", type=int, default=10)
    parser.add_argument("--strategy", default="HIGH_OF_DAY_BREAKOUT")
    parser.add_argument("--policy-set", choices=tuple(POLICY_SETS), default="BASELINE")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--max-per-symbol", type=int, default=100)
    parser.add_argument("--hold-minutes", type=int, default=60)
    parser.add_argument("--risk-dollars", type=D, default=D("25"))
    parser.add_argument("--cost-per-share-per-side", type=D, default=D("0.01"))
    args = parser.parse_args(argv)
    policies = POLICY_SETS[args.policy_set]
    policy_version = ("CHRONOLOGICAL_BAR_EXITS_V1" if args.policy_set == "BASELINE"
                      else "EARLY_PARTIAL_BAR_EXITS_V1")
    if not 0 < args.max_scan <= 1000 or not 0 < args.max_episodes <= 100:
        parser.error("Use at most 1000 scanned rows and 100 episodes per batch")
    if not 0 < args.max_per_symbol <= 100:
        parser.error("max-per-symbol must be between 1 and 100")
    if not args.risk_dollars.is_finite() or args.risk_dollars <= 0:
        parser.error("risk-dollars must be positive and finite")
    if args.hold_minutes <= 0 or not args.cost_per_share_per_side.is_finite() or args.cost_per_share_per_side < 0:
        parser.error("hold-minutes must be positive; costs must be finite and nonnegative")
    source_path = args.root / "corpus_repaired/episodes.jsonl"
    if args.checkpoint and args.checkpoint.resolve() == source_path.resolve():
        parser.error("Checkpoint must not overwrite the episode source")
    stat = source_path.stat()
    identity = {"path": str(source_path.resolve()), "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns}
    configuration = {"strategy": args.strategy, "hold_minutes": args.hold_minutes,
                     "risk_dollars": str(args.risk_dollars),
                     "cost_per_share_per_side": str(args.cost_per_share_per_side),
                     "max_per_symbol": args.max_per_symbol,
                     "policy_version": policy_version}
    if args.policy_set != "BASELINE":
        configuration.update(policy_set=args.policy_set,
            policies=json.loads(json.dumps([asdict(p) for p in policies], default=str)))
    offset = source_rows = 0
    if args.checkpoint and args.checkpoint.exists():
        saved = json.loads(args.checkpoint.read_text(encoding="utf-8"))
        if saved.get("source") != identity or saved.get("configuration") != configuration:
            parser.error("Checkpoint source or policy configuration changed; use a new checkpoint")
        offset, source_rows = saved["next_byte_offset"], saved["source_rows_consumed"]
        if not isinstance(offset, int) or not 0 <= offset <= stat.st_size:
            parser.error("Invalid checkpoint byte offset")
        if not isinstance(source_rows, int) or source_rows < 0:
            parser.error("Invalid checkpoint row count")
    selected = scanned = paired = 0
    coverage = coverage_bucket(policies)
    early_comparison = (coverage_bucket(EARLY_PARTIAL_POLICIES)
                        if args.policy_set == "EARLY_PARTIAL_V1" else None)
    grouped = {}
    skipped = Counter()
    symbol_counts = Counter()
    partition_hashes = {}
    details = []
    totals = {p.name: D(0) for p in policies}
    print("EVIDENCE: MINUTE_BAR_PROXY_FIXED_PLANNED_ENTRY_NOT_ACTUAL_FILLS")
    print("PORTFOLIO: INDEPENDENT_EPISODES_NO_SHARED_CAPITAL_OR_OVERLAP_MODEL")
    with source_path.open("rb") as handle:
        if offset and offset < stat.st_size:
            handle.seek(offset - 1)
            if handle.read(1) != b"\n":
                parser.error("Checkpoint must point to a complete line boundary")
        handle.seek(offset)
        for _ in range(args.max_scan):
            line = handle.readline(1_000_001)
            if not line:
                break
            if len(line) > 1_000_000:
                raise ValueError("OVERSIZED_EPISODE_ROW")
            scanned += 1
            row = json.loads(line)
            memberships = tuple(sorted(set(row.get("strategy_memberships", ()))))
            if not memberships or (args.strategy != "ALL" and args.strategy not in memberships):
                skipped["STRATEGY_NOT_SELECTED"] += 1
                continue
            symbol = row["symbol"]
            if symbol_counts[symbol] >= args.max_per_symbol:
                skipped["PER_SYMBOL_BATCH_LIMIT"] += 1
                continue
            path = args.root / "normalized" / f"{symbol}_{row['trading_date']}.jsonl"
            entry_price, stop = D(str(row["trigger_price"])), D(str(row["structural_stop"]))
            if not entry_price > stop > 0 or not path.is_file():
                skipped["INVALID_GEOMETRY_OR_MISSING_PARTITION"] += 1
                continue
            qty = (args.risk_dollars / (entry_price - stop)).to_integral_value(rounding=ROUND_FLOOR)
            if not 0 < qty <= 10000:
                skipped["QUANTITY_OUTSIDE_LIMIT"] += 1
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
            results = compare(bars, policies=policies, symbol=symbol,
                entry_time=datetime.fromisoformat(row["detected_timestamp"]),
                entry_price=entry_price, initial_stop=stop, quantity=qty,
                hold_minutes=args.hold_minutes,
                cost_per_share_per_side=args.cost_per_share_per_side)
            selected += 1
            symbol_counts[symbol] += 1
            partition_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            record_coverage(coverage, results)
            if early_comparison is not None:
                names = {p.name for p in EARLY_PARTIAL_POLICIES}
                record_coverage(early_comparison,
                                tuple(r for r in results if r.policy in names))
            for strategy in memberships if args.strategy == "ALL" else (args.strategy,):
                key = (row["trading_date"], strategy)
                record_coverage(grouped.setdefault(key, coverage_bucket(policies)), results)
            detail = {"episode_id": row["episode_id"], "symbol": symbol,
                "normalized_sha256": partition_hashes[path.name],
                "policy_version": policy_version,
                "hold_minutes": args.hold_minutes,
                "cost_per_share_per_side": args.cost_per_share_per_side,
                "entry_time": row["detected_timestamp"], "entry_price": entry_price,
                "initial_stop": stop, "quantity": qty,
                "results": [asdict(r) for r in results]}
            if args.report_dir:
                details.append(detail)
            if not args.summary_only:
                print(json.dumps(detail, default=str))
            if all(r.status == "CLOSED" for r in results):
                paired += 1
                for r in results:
                    totals[r.policy] += r.net_pnl
            if selected >= args.max_episodes:
                break
        next_offset = handle.tell()
    if source_path.stat().st_size != stat.st_size or source_path.stat().st_mtime_ns != stat.st_mtime_ns:
        raise ValueError("SOURCE_CHANGED_DURING_BATCH")
    summary = {"scanned_rows": scanned, "selected_episodes": selected,
        "paired_closed_episodes": paired, "excluded_from_paired_totals": selected - paired,
        "paired_pnl_totals": totals,
        "coverage": coverage, "skipped_rows": skipped,
        "normalized_partition_hashes": partition_hashes,
        "by_date_strategy": [{"date": date, "strategy": strategy, **bucket}
                             for (date, strategy), bucket in sorted(grouped.items())],
        "source": identity, "configuration": configuration,
        "batch_limits": {"max_scan": args.max_scan, "max_episodes": args.max_episodes},
        "start_byte_offset": offset, "next_byte_offset": next_offset,
        "source_rows_consumed": source_rows + scanned, "at_eof": next_offset == stat.st_size,
        "selection": "BOUNDED_FILE_ORDER_SAMPLE_NOT_REPRESENTATIVE_PERFORMANCE",
        "grouping": "OVERLAPPING_STRATEGY_MEMBERSHIPS_DO_NOT_SUM_GROUPS"}
    if early_comparison is not None:
        summary["early_partial_comparison"] = {
            **early_comparison,
            "excluded_from_paired_totals": selected - early_comparison["paired_closed_episodes"],
            "cohort": "BOTH_QUARTER_PARTIAL_POLICIES_CLOSED_SAME_EPISODES",
            "scope": "PARTIAL_THRESHOLD_MECHANICS_NOT_EXACT_WARRIOR_HARVEST"}
    if args.report_dir:
        save_json(args.report_dir / f"coverage_{offset}_{next_offset}.json",
                  {**summary, "episodes": details})
    if args.checkpoint:
        save_json(args.checkpoint, summary)
    print(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

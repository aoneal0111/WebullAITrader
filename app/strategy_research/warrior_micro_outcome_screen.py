"""Read-only, cost-sensitive *hypothetical* 1R barrier screen for Micro Pullback.

No broker quotes or fills. Uses only completed signal bars; the next observed
consecutive minute OPEN is a hypothetical entry, not a tradable ask. Same-bar
stop and target hits are explicitly ambiguous and never counted as wins.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import re

from app.strategies.warrior_momentum.models import MinuteBar, SetupState
from app.strategies.warrior_momentum.setups import detect_micro_pullback
from .episode_price_study import BarCache

MAX_REPORTS = 600
MAX_HORIZON = 120


def outcome(bars: tuple[MinuteBar, ...], trigger_index: int, *,
            structural_stop: Decimal, horizon_bars: int = 30,
            cost_per_side_per_share: Decimal = Decimal("0")) -> dict:
    """Hypothetical entry at next consecutive bar open; fixed 1R bracket."""
    if not 1 <= horizon_bars <= MAX_HORIZON:
        raise ValueError("INVALID_HORIZON")
    if not isinstance(cost_per_side_per_share, Decimal) or (
        not cost_per_side_per_share.is_finite() or cost_per_side_per_share < 0
    ):
        raise ValueError("INVALID_COST")
    if (not isinstance(structural_stop, Decimal) or
            not structural_stop.is_finite() or structural_stop <= 0):
        raise ValueError("INVALID_STOP")
    if not 0 <= trigger_index < len(bars):
        raise ValueError("INVALID_TRIGGER_INDEX")
    next_index = trigger_index + 1
    if (next_index >= len(bars) or
            bars[next_index].timestamp - bars[trigger_index].timestamp != timedelta(minutes=1)):
        return {"status": "NO_CONSECUTIVE_NEXT_OPEN", "hypothetical_net_r": None}
    entry = bars[next_index].open
    if not entry.is_finite() or entry <= structural_stop:
        return {"status": "INVALID_NEXT_OPEN_RISK", "hypothetical_net_r": None}
    risk = entry - structural_stop
    target = entry + risk
    expected_time = bars[next_index].timestamp
    for index in range(next_index, min(len(bars), next_index + horizon_bars)):
        bar = bars[index]
        if bar.timestamp != expected_time:
            return {"status": "GAP_CENSORED", "hypothetical_net_r": None}
        expected_time += timedelta(minutes=1)
        if not all(x.is_finite() for x in (bar.high, bar.low)):
            return {"status": "INVALID_FUTURE_BAR", "hypothetical_net_r": None}
        stopped = bar.low <= structural_stop
        targeted = bar.high >= target
        if stopped and targeted:
            return {"status": "AMBIGUOUS_SAME_BAR", "hypothetical_net_r": None}
        if stopped or targeted:
            gross = Decimal("-1") if stopped else Decimal("1")
            net = gross - Decimal("2") * cost_per_side_per_share / risk
            return {
                "status": "STOP_FIRST_OBSERVED" if stopped else "TARGET_FIRST_OBSERVED",
                "hypothetical_net_r": str(net),
                "bars_until_observation": index - next_index + 1,
                "entry_open": str(entry), "structural_stop": str(structural_stop),
                "target": str(target),
                "assumption": "HYPOTHETICAL_NEXT_MINUTE_OPEN_NO_BID_ASK_OR_FILL_PROOF",
            }
    return {"status": "HORIZON_CENSORED", "hypothetical_net_r": None}


def study(reports: Path, normalized: Path, *, horizon_bars=30,
          cost_per_side_per_share=Decimal("0")) -> dict:
    if not 1 <= horizon_bars <= MAX_HORIZON:
        raise ValueError("INVALID_HORIZON")
    if (not isinstance(cost_per_side_per_share, Decimal) or
            not cost_per_side_per_share.is_finite() or cost_per_side_per_share < 0):
        raise ValueError("INVALID_COST")
    paths = sorted(Path(reports).glob("*-timing.json"))
    if not 1 <= len(paths) <= MAX_REPORTS:
        raise ValueError("REPORT_COUNT_BOUND")
    cache = BarCache(Path(normalized))
    results = []
    seen = set()
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
            raise ValueError("INVALID_REPORT_FILE")
        report = json.loads(path.read_text(encoding="utf-8"))
        symbol, day = report.get("symbol"), report.get("day")
        if (report.get("scope") != "WARRIOR_MICRO_SELECTION_OFFLINE_V1"
                or not isinstance(symbol, str)
                or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", symbol)
                or not isinstance(day, str)
                or not re.fullmatch(r"2026-\d{2}-\d{2}", day)
                or path.name != f"{symbol}_{day}-timing.json"
                or (symbol, day) in seen):
            raise ValueError("REPORT_PROVENANCE_INVALID")
        seen.add((symbol, day))
        original = cache.get(symbol, day)
        cache.verify_stable()
        if cache.proofs[(symbol, day)][2].lower() != str(report.get("source_sha256", "")).lower():
            raise ValueError("SOURCE_SHA256_MISMATCH")
        if len(original) > 1440 or len(original) != report.get("bars"):
            raise ValueError("SOURCE_BAR_COUNT_MISMATCH")
        bars = tuple(MinuteBar(b.symbol, b.timestamp, b.open, b.high, b.low, b.close, b.volume)
                     for b in original)
        index_by_close = {bar.timestamp + timedelta(minutes=1): i
                          for i, bar in enumerate(bars)}
        if len(index_by_close) != len(bars):
            raise ValueError("BAR_TIMESTAMP_DUPLICATE")
        rows = report.get("observations")
        if not isinstance(rows, list) or len(rows) > 1440:
            raise ValueError("TRIGGER_REPORT_BOUND")
        prior = None
        for row in rows:
            from datetime import datetime
            at = datetime.fromisoformat(row["at"])
            if at.utcoffset() is None or at not in index_by_close:
                raise ValueError("TRIGGER_TIME_NOT_IN_SOURCE")
            if prior is not None and at <= prior:
                raise ValueError("TRIGGER_TIME_NOT_ORDERED")
            first_of_run = prior is None or at - prior > timedelta(minutes=1)
            prior = at
            if not first_of_run:
                continue
            index = index_by_close[at]
            setup = detect_micro_pullback(bars[:index + 1])
            if (setup.state is not SetupState.TRIGGERED or setup.stop_price is None
                    or setup.trigger != Decimal(str(row.get("micro_trigger")))):
                raise ValueError("TRIGGER_SOURCE_MISMATCH")
            decision = outcome(bars, index, structural_stop=setup.stop_price,
                               horizon_bars=horizon_bars,
                               cost_per_side_per_share=cost_per_side_per_share)
            results.append({"symbol": symbol, "day": day,
                            "signal_close_at": at.isoformat(), **decision})
    cache.verify_stable()
    counts = Counter(row["status"] for row in results)
    resolved = [Decimal(row["hypothetical_net_r"]) for row in results
                if row["hypothetical_net_r"] is not None]
    return {
        "scope": "RESEARCH_ONLY_WARRIOR_MICRO_NEXT_OPEN_BARRIER_V1",
        "source_report_count": len(paths),
        "observed_trigger_runs": len(results),
        "outcomes": dict(counts),
        "resolved_barrier_observations": len(resolved),
        "hypothetical_net_r_sum_resolved_only": str(sum(resolved, Decimal(0))),
        "horizon_bars": horizon_bars,
        "cost_per_side_per_share": str(cost_per_side_per_share),
        "cases": results,
        "orders": 0, "network_requests": 0, "production_policy_changed": False,
        "limitations": [
            "Next-minute OPEN is hypothetical, not a broker ask or demonstrated fill",
            "Market orders, slippage, spread and depth are not modeled",
            "Same-bar stop/target collisions remain ambiguous and excluded from resolved counts",
            "Missing intervals and horizon ends are censored, never presumed wins",
            "Chosen historical partitions are retrospective, not an independent holdout",
            "Repeated trigger assessments are grouped into consecutive observed runs, not validated trades",
            "No profitability or realized win-rate estimate, no automatic strategy promotion",
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reports", type=Path, required=True)
    p.add_argument("--normalized", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--horizon-bars", type=int, default=30)
    p.add_argument("--cost-per-side-per-share", type=Decimal, default=Decimal("0"))
    args = p.parse_args()
    result = study(args.reports, args.normalized,
                   horizon_bars=args.horizon_bars,
                   cost_per_side_per_share=args.cost_per_side_per_share)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, indent=2)
    print("OBSERVED_TRIGGER_RUNS=" + str(result["observed_trigger_runs"]))
    print("RESOLVED_BARRIER_OBSERVATIONS=" + str(result["resolved_barrier_observations"]))
    print("REPORT=" + str(args.output))


if __name__ == "__main__":
    main()

"""Audit cost sensitivity of an existing hypothetical Warrior Micro barrier report.

This is a *recalculation*, not replay, strategy optimization, win-rate
promotion, or evidence of executable fills. No source data is overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
from pathlib import Path

SCOPE = "WARRIOR_MICRO_COST_SENSITIVITY_V1"
SOURCE = "RESEARCH_ONLY_WARRIOR_MICRO_NEXT_OPEN_BARRIER_V1"
MAX_BYTES = 4_000_000
MAX_CASES = 600
DEFAULT_COSTS = ("0", "0.0025", "0.005", "0.01", "0.02", "0.03")
WIN = "TARGET_FIRST_OBSERVED"
LOSS = "STOP_FIRST_OBSERVED"
UNRESOLVED = frozenset({
    "NO_CONSECUTIVE_NEXT_OPEN", "HORIZON_CENSORED",
    "GAP_CENSORED", "AMBIGUOUS_SAME_BAR",
    "INVALID_NEXT_OPEN_RISK", "INVALID_FUTURE_BAR",
})


def _decimal(value):
    try:
        item = Decimal(str(value))
    except Exception as exc:
        raise ValueError("INVALID_NUMERIC_SOURCE") from exc
    if not item.is_finite():
        raise ValueError("NONFINITE_NUMERIC_SOURCE")
    return item


def analyze(source, *, costs=DEFAULT_COSTS):
    if not isinstance(source, dict) or source.get("scope") != SOURCE:
        raise ValueError("EXPECTED_HYPOTHETICAL_BARRIER_REPORT")
    if (source.get("orders") != 0 or source.get("network_requests") != 0
            or source.get("production_policy_changed") is not False):
        raise ValueError("SOURCE_SIDE_EFFECT_SCOPE_UNKNOWN")
    cases = source.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("BOUNDED_CASES_REQUIRED")
    if len(costs) < 1 or len(costs) > 12:
        raise ValueError("COST_SCENARIO_COUNT_BOUND")
    costs = [_decimal(cost) for cost in costs]
    if any(cost < 0 for cost in costs) or len(set(costs)) != len(costs):
        raise ValueError("INVALID_COST_SCENARIOS")
    baseline = _decimal(source.get("cost_per_side_per_share"))
    if baseline < 0:
        raise ValueError("NEGATIVE_BASELINE_COST")
    seen = set()
    distribution = Counter()
    risk_values = []
    daily = defaultdict(lambda: {"resolved": 0, "wins": 0, "gross_r": Decimal(0),
                                  "cost_exposure_inverse_r": Decimal(0)})
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("INVALID_CASE_RECORD")
        key = (case.get("symbol"), case.get("day"), case.get("signal_close_at"))
        if not all(isinstance(x, str) and x for x in key) or key in seen:
            raise ValueError("DUPLICATE_OR_UNIDENTIFIED_CASE")
        seen.add(key)
        status = case.get("status")
        if status not in (WIN, LOSS) and status not in UNRESOLVED:
            raise ValueError("UNKNOWN_OUTCOME")
        distribution[status] += 1
        reported = case.get("hypothetical_net_r")
        if status in UNRESOLVED:
            if reported is not None:
                raise ValueError("UNRESOLVED_CASE_HAS_NET_R")
            continue
        entry = _decimal(case.get("entry_open"))
        stop = _decimal(case.get("structural_stop"))
        target = _decimal(case.get("target"))
        risk = entry - stop
        if entry <= 0 or stop <= 0 or risk <= 0 or target != entry + risk:
            raise ValueError("INVALID_1R_BRACKET_GEOMETRY")
        gross = Decimal(1) if status == WIN else Decimal(-1)
        expected = gross - 2 * baseline / risk
        if abs(expected - _decimal(reported)) > Decimal("0.000000000001"):
            raise ValueError("SOURCE_NET_R_RECONCILIATION_FAILED")
        risk_values.append((gross, risk))
        day = daily[case["day"]]
        day["resolved"] += 1
        day["wins"] += int(status == WIN)
        day["gross_r"] += gross
        day["cost_exposure_inverse_r"] += Decimal(2) / risk

    resolved = len(risk_values)
    if source.get("observed_trigger_runs") != len(cases) or (
        source.get("resolved_barrier_observations") != resolved
    ):
        raise ValueError("SOURCE_CASE_COUNT_MISMATCH")
    original_counts = source.get("outcomes")
    if not isinstance(original_counts, dict) or dict(distribution) != original_counts:
        raise ValueError("OUTCOME_COUNTS_MISMATCH")
    if (abs(sum((gross - 2 * baseline / risk for gross, risk in risk_values), Decimal(0))
            - _decimal(source.get("hypothetical_net_r_sum_resolved_only")))
            > Decimal("0.000000000001")):
        raise ValueError("SOURCE_TOTAL_NET_R_RECONCILIATION_FAILED")
    wins = distribution[WIN]
    losses = distribution[LOSS]
    gross_total = Decimal(wins - losses)
    cost_exposure = sum((2 / risk for _, risk in risk_values), Decimal(0))
    break_even = gross_total / cost_exposure if cost_exposure > 0 else None
    scenarios = []
    for cost in costs:
        total = gross_total - cost * cost_exposure
        scenarios.append({
            "assumed_cost_per_side_per_share": str(cost),
            "resolved_only_net_r": str(total),
            "resolved_only_mean_net_r": None if not resolved else str(total / resolved),
            "profitable_resolved_only": total > 0,
        })
    return {
        "scope": SCOPE,
        "source_scope": SOURCE,
        "observed_trigger_runs": len(cases),
        "resolved_cases": resolved,
        "unresolved_cases": len(cases) - resolved,
        "resolved_win_count": wins,
        "resolved_loss_count": losses,
        "resolved_only_win_fraction": None if not resolved else str(Decimal(wins) / resolved),
        "gross_resolved_only_r": str(gross_total),
        "assumed_cost_per_side_share_break_even_r": (
            None if break_even is None else str(break_even)
        ),
        "baseline_assumed_cost_per_side_per_share": str(baseline),
        "baseline_reconciled_net_r": str(gross_total - baseline * cost_exposure),
        "scenarios": scenarios,
        "daily_group_counts": [
            {"day": day, "resolved_cases": row["resolved"], "wins": row["wins"],
             "gross_r": str(row["gross_r"]),
             "baseline_net_r": str(row["gross_r"] - baseline * row["cost_exposure_inverse_r"])}
            for day, row in sorted(daily.items())
        ],
        "orders": 0,
        "network_requests": 0,
        "production_policy_changed": False,
        "limitations": [
            "Hypothetical next-minute OPEN prices are neither market quotes nor executable entries",
            "The 1R resolution uses future minute highs/lows, not actual exits or fills",
            "Win fraction and R totals exclude unresolved, ambiguous and censored observations",
            "Cost scenarios assume per-share-per-side linear costs only; slippage/depth/spread not verified",
            "Retrospectively selected episodes are not independent trades or out-of-sample",
            "No live strategy promotion or causal profitability claim",
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if (a.source.is_symlink() or not a.source.is_file() or
            a.source.stat().st_size > MAX_BYTES):
        raise ValueError("BOUNDED_REPORT_REQUIRED")
    data = json.loads(a.source.read_text(encoding="utf-8-sig"))
    result = analyze(data)
    with a.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, indent=2)
    print("RESOLVED=" + str(result["resolved_cases"]))
    print("UNRESOLVED=" + str(result["unresolved_cases"]))
    print("BREAK_EVEN_COST_PER_SIDE_SHARE=" + str(result["assumed_cost_per_side_share_break_even_r"]))
    print("REPORT=" + str(a.output))


if __name__ == "__main__":
    main()

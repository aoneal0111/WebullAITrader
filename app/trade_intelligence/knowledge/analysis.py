"""Offline cohort, chronological split, and research-only simulations."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from statistics import median
from typing import Iterable

PROFIT_RESEARCH_VERSION = "ATLAS_PROFIT_RESEARCH_V1"
REENTRY_TRANSITION_VERSION = "ATLAS_REENTRY_TRANSITIONS_V1"
PERCENT_PARTIAL_TARGETS = (2, 3, 5, 8, 10)
R_TARGETS = (1, 1.5, 2, 3)


GROUP_DIMENSIONS = (
    "strategy", "primary_strategy", "strategy_combination", "membership_count",
    "time_of_day_bucket", "price_bucket", "gap_bucket", "volume_behavior_bucket",
    "volatility_bucket", "pullback_depth_bucket", "distance_from_vwap_bucket",
    "distance_from_hod_bucket", "extension_bucket", "setup_duration_bucket",
    "provider", "feed", "source_quality",
)


def _values(row: dict) -> dict:
    return row.get("features", {}).get("values", {})


def _bucket(value, *, step: float = 5.0) -> str | None:
    if value is None:
        return None
    number = float(value)
    low = int(number // step) * int(step)
    return f"{low}:{low + int(step)}"


def _dimension(row: dict, name: str):
    values = _values(row)
    if name == "strategy":
        return tuple(row.get("strategy_memberships", ()))
    if name == "primary_strategy":
        return row.get("primary_strategy")
    if name == "strategy_combination":
        return values.get("strategy_combination") or "+".join(sorted(row.get("strategy_memberships", ())))
    if name == "membership_count":
        return len(row.get("strategy_memberships", ()))
    if name == "time_of_day_bucket":
        return values.get("time_of_day_bucket")
    if name in ("provider", "feed"):
        return row.get("provenance", {}).get(name)
    if name == "source_quality":
        return "PRELIMINARY_SINGLE_EXCHANGE" if row.get("provenance", {}).get("feed") == "IEX" else None
    source = values.get("extension", {})
    if name == "price_bucket":
        return _bucket(row.get("price_at_detection"), step=5)
    if name == "gap_bucket":
        return _bucket(row.get("gap_percent"), step=2)
    if name == "pullback_depth_bucket":
        return _bucket(values.get("maturation", {}).get("pullback_depth_percent"), step=5)
    if name == "distance_from_vwap_bucket":
        return _bucket(source.get("distance_from_vwap_percent"), step=2)
    if name == "distance_from_hod_bucket":
        return _bucket(source.get("distance_from_hod_percent"), step=2)
    if name == "extension_bucket":
        return _bucket(source.get("distance_from_trigger_percent"), step=2)
    if name == "setup_duration_bucket":
        return _bucket(values.get("maturation", {}).get("seconds_since_impulse_start"), step=300)
    return values.get(name)


def _metric_numbers(rows: list[dict], path: tuple[str, ...]) -> list[float]:
    result = []
    for row in rows:
        value = row.get("outcomes", {})
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None:
            try:
                result.append(float(value))
            except (TypeError, ValueError):
                pass
    return result


def cohort_report(rows: Iterable[dict], group_by: Iterable[str], *, min_sample: int = 30,
                  concentration_threshold: float = .5) -> list[dict]:
    dimensions = tuple(group_by)
    if any(item not in GROUP_DIMENSIONS for item in dimensions):
        raise ValueError("UNKNOWN_GROUP_DIMENSION")
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        keys = []
        for dim in dimensions:
            value = _dimension(row, dim)
            keys.append(value if dim != "strategy" else value)
        # A strategy grouping explodes memberships while retaining one payload.
        if "strategy" in dimensions:
            index = dimensions.index("strategy")
            for strategy in keys[index]:
                clone = list(keys); clone[index] = strategy
                grouped[tuple(clone)].append(row)
        else:
            grouped[tuple(keys)].append(row)
    output = []
    for key, members in sorted(grouped.items(), key=lambda item: tuple(str(x) for x in item[0])):
        n = len(members)
        def share(field, value):
            return sum(item.get(field) == value for item in members) / n if n else 0
        counts = Counter(item.get("symbol") for item in members)
        dates = Counter(item.get("trading_date") for item in members)
        months = Counter(str(item.get("trading_date", ""))[:7] for item in members)
        flags = []
        if counts and max(counts.values()) / n >= concentration_threshold: flags.append("SYMBOL_CONCENTRATED")
        if dates and max(dates.values()) / n >= concentration_threshold: flags.append("DATE_CONCENTRATED")
        if months and max(months.values()) / n >= concentration_threshold: flags.append("TEMPORALLY_CONCENTRATED")
        target_rates = {str(p): sum(bool(item.get("outcomes", {}).get("percent_targets", {}).get(str(p), {}).get("hit")) for item in members) / n for p in (2, 3, 5, 8, 10)}
        times = {str(p): [float(item["outcomes"]["percent_targets"][str(p)]["elapsed_seconds"]) for item in members if item.get("outcomes", {}).get("percent_targets", {}).get(str(p), {}).get("elapsed_seconds") is not None] for p in (2, 3, 5, 8, 10)}
        output.append({"group": dict(zip(dimensions, key)), "sample_count": n,
                       "confidence_state": "INSUFFICIENT_SAMPLE" if n < min_sample else "EARLY" if n < 100 else "MODERATE" if n < 500 else "LARGE_SAMPLE",
                       "unique_symbols": len(counts), "unique_dates": len(dates),
                       "median_mfe": median(_metric_numbers(members, ("mfe_percent",))) if _metric_numbers(members, ("mfe_percent",)) else None,
                       "median_mae": median(_metric_numbers(members, ("mae_percent",))) if _metric_numbers(members, ("mae_percent",)) else None,
                       "median_maximum_r": median(_metric_numbers(members, ("maximum_R",))) if _metric_numbers(members, ("maximum_R",)) else None,
                       "target_hit_rates": target_rates,
                       "stop_first_rates": {str(p): sum(item.get("outcomes", {}).get("percent_targets", {}).get(str(p), {}).get("first_plan_event") == "STOP_FIRST" for item in members) / n for p in (2, 3, 5, 8, 10)},
                       "median_time_to_target_seconds": {p: median(value) if value else None for p, value in times.items()},
                       "reentry_frequency": sum(bool(item.get("reentry")) for item in members) / n,
                       "largest_symbol_share": max(counts.values(), default=0) / n,
                       "largest_date_share": max(dates.values(), default=0) / n,
                       "largest_month_share": max(months.values(), default=0) / n,
                       "concentration_flags": flags})
    return output


def chronological_splits(rows: Iterable[dict], *, train_end: str | None = None,
                         validation_end: str | None = None, ratios=(.6, .2, .2)) -> dict[str, list[dict]]:
    ordered = sorted(rows, key=lambda row: (row.get("trading_date", ""), row.get("episode_id", "")))
    dates = sorted({row.get("trading_date") for row in ordered})
    if train_end or validation_end:
        result = {"TRAIN": [], "VALIDATION": [], "TEST": []}
        for row in ordered:
            day = row.get("trading_date")
            bucket = "TRAIN" if train_end and day <= train_end else "VALIDATION" if validation_end and day <= validation_end else "TEST"
            result[bucket].append(row)
        return result
    if not dates: return {"TRAIN": [], "VALIDATION": [], "TEST": []}
    first = max(1, int(len(dates) * ratios[0])); second = max(first + 1, int(len(dates) * (ratios[0] + ratios[1])))
    bounds = (dates[min(first - 1, len(dates) - 1)], dates[min(second - 1, len(dates) - 1)])
    return chronological_splits(ordered, train_end=bounds[0], validation_end=bounds[1])


def walk_forward_folds(rows: Iterable[dict], *, train_dates: int, validation_dates: int,
                       test_dates: int = 0, step: int | None = None) -> list[dict]:
    rows = tuple(rows)
    dates = sorted({row.get("trading_date") for row in rows})
    step = step or validation_dates
    folds = []
    for start in range(0, max(0, len(dates) - train_dates - validation_dates - test_dates + 1), step):
        train = dates[start:start + train_dates]; validation = dates[start + train_dates:start + train_dates + validation_dates]
        test = dates[start + train_dates + validation_dates:start + train_dates + validation_dates + test_dates]
        folds.append({"train_start": train[0], "train_end": train[-1], "validation_start": validation[0], "validation_end": validation[-1],
                      "test_start": test[0] if test else None, "test_end": test[-1] if test else None,
                      "train_count": sum(row.get("trading_date") in train for row in rows),
                      "validation_count": sum(row.get("trading_date") in validation for row in rows),
                      "test_count": sum(row.get("trading_date") in test for row in rows)})
    return folds


def entry_delay_research(row: dict) -> dict:
    return {"trigger_reference": {"status": "SIMULATED"}, "next_bar_open": {"status": "SIMULATED"},
            "next_bar_close": {"status": "SIMULATED"}, "+1_completed_bar": {"status": "SIMULATED"},
            "+2_completed_bars": {"status": "SIMULATED"}, "sub_minute": "UNAVAILABLE_AT_1MIN_RESOLUTION"}


def profit_management_research(row: dict, target: int = 5, partial_percent: int = 50) -> dict:
    return simulate_profit_policy(row, target_percent=target, partial_percent=partial_percent)


def _event(outcomes: dict, percent: int):
    return outcomes.get("percent_targets", {}).get(str(percent), {})


def simulate_profit_policy(row: dict, *, target_percent: int = 5, partial_percent: int = 50,
                           runner_terminal: str = "SESSION_CLOSE", runner_target_percent: int | None = None,
                           target_r: float | None = None) -> dict:
    """Resolve a bounded policy from frozen historical outcome labels.

    This intentionally does not invent fills or intrabar ordering.  A policy
    is marked ambiguous whenever the underlying target event is ambiguous.
    """
    if target_percent not in PERCENT_PARTIAL_TARGETS and target_r is None:
        raise ValueError("UNSUPPORTED_POLICY_TARGET")
    if partial_percent not in (0, 25, 50, 75, 100):
        raise ValueError("UNSUPPORTED_PARTIAL_SIZE")
    outcomes = row.get("outcomes", {})
    event = _event(outcomes, target_percent) if target_r is None else outcomes.get("r_targets", {}).get(str(target_r), {})
    ambiguity = event.get("first_plan_event") == "INTRABAR_ORDER_UNKNOWN"
    terminal = "INTRABAR_ORDER_UNKNOWN" if ambiguity else runner_terminal
    runner_event = _event(outcomes, runner_target_percent) if runner_target_percent is not None else {}
    if not ambiguity and runner_target_percent is not None and runner_event.get("first_plan_event") == "INTRABAR_ORDER_UNKNOWN":
        terminal = "INTRABAR_ORDER_UNKNOWN"
    target_return = float(target_percent) if target_r is None else None
    target_r_return = float(target_r) if target_r is not None else None
    runner_return = None if terminal == "INTRABAR_ORDER_UNKNOWN" else (
        float(runner_target_percent) if runner_target_percent is not None and runner_event.get("hit") else
        float(outcomes.get("horizons", {}).get("3600", {}).get("mfe_percent")) if terminal == "SESSION_CLOSE" and outcomes.get("horizons", {}).get("3600", {}).get("mfe_percent") is not None else None)
    resolved = terminal != "INTRABAR_ORDER_UNKNOWN" and (event.get("hit") or partial_percent == 0)
    realized = (partial_percent / 100) * target_return if resolved and target_return is not None else None
    realized_r = (partial_percent / 100) * target_r_return if resolved and target_r_return is not None else None
    total = None if realized is None and runner_return is None else (realized or 0) + ((100 - partial_percent) / 100) * (runner_return or 0)
    return {"research_version": PROFIT_RESEARCH_VERSION, "simulation": "SIMULATED", "fill_status": "NOT_ACTUAL_FILL",
            "policy": {"partial_percent": partial_percent, "target_percent": target_percent,
                        "target_r": target_r, "runner_terminal": runner_terminal,
                        "runner_target_percent": runner_target_percent},
            "gross_return_percent": total, "realized_partial_return": realized,
            "realized_partial_r": realized_r, "r_return": None if realized_r is None else realized_r + ((100 - partial_percent) / 100) * (runner_return or 0),
            "runner_return": runner_return, "total_simulated_return": total,
            "resolvable": resolved, "ambiguity_state": terminal,
            "time_to_first_partial": event.get("elapsed_seconds") if resolved else None,
            "terminal_reason": terminal}


def runner_path_analysis(row: dict, target_percent: int) -> dict:
    event = _event(row.get("outcomes", {}), target_percent)
    post = row.get("outcomes", {}).get("post_" + str(target_percent), {})
    return {"research_version": PROFIT_RESEARCH_VERSION, "target_percent": target_percent,
            "hit": bool(event.get("hit")), "post_target_MFE": post.get("mfe_after_target", post.get("maximum_giveback_after_8")),
            "post_target_MAE": post.get("mae_after_target"), "time_to_peak_after_target": post.get("time_to_peak_after_target"),
            "maximum_giveback_from_peak": post.get("maximum_giveback_after_8"),
            "ending_return_at_session_close": row.get("outcomes", {}).get("horizons", {}).get("3600", {}).get("mfe_percent"),
            "ambiguity_state": event.get("first_plan_event") if event.get("first_plan_event") == "INTRABAR_ORDER_UNKNOWN" else None}


def capital_scenarios(simulated_return_percent: float | None, capitals=(1000, 5000, 10000, 20000, 50000, 100000)) -> dict[str, float | None]:
    return {str(capital): None if simulated_return_percent is None else capital * simulated_return_percent / 100 for capital in capitals}


def constant_risk_scenarios(entry: float, stop: float, risk_budgets=(25, 50, 100, 200, 500)) -> dict[str, dict[str, float]]:
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        raise ValueError("NON_POSITIVE_RISK")
    return {str(budget): {"risk_budget": float(budget), "shares": float(budget / risk_per_share)} for budget in risk_budgets}


def transition_record(parent: dict, child: dict, *, between_bars: Iterable[dict] = ()) -> dict:
    """Create one compact parent/child record; child payload is never copied."""
    if parent.get("episode_id") == child.get("episode_id") or child.get("structural_anchor") == parent.get("structural_anchor"):
        raise ValueError("SAME_STRUCTURAL_ANCHOR_NOT_REENTRY")
    parent_time = datetime.fromisoformat(parent["detected_timestamp"])
    child_time = datetime.fromisoformat(child["detected_timestamp"])
    if child_time <= parent_time:
        raise ValueError("REENTRY_NOT_LATER")
    between = tuple(between_bars)
    prices = [float(item.get("high")) for item in between if item.get("high") is not None]
    lows = [float(item.get("low")) for item in between if item.get("low") is not None]
    parent_peak = float(parent.get("trigger_price", 0)) + float(parent.get("outcomes", {}).get("mfe_per_share", 0) or 0)
    child_trigger = float(child.get("trigger_price", 0))
    return {"research_version": REENTRY_TRANSITION_VERSION, "parent_episode_id": parent["episode_id"],
            "child_episode_id": child["episode_id"], "parent_primary_strategy": parent.get("primary_strategy"),
            "parent_strategy_combination": "+".join(sorted(parent.get("strategy_memberships", ()))),
            "child_primary_strategy": child.get("primary_strategy"),
            "child_strategy_combination": "+".join(sorted(child.get("strategy_memberships", ()))),
            "parent_trigger": parent.get("trigger_price"), "parent_stop": parent.get("structural_stop"),
            "child_trigger": child.get("trigger_price"), "child_stop": child.get("structural_stop"),
            "parent_MFE": parent.get("outcomes", {}).get("mfe_percent"), "parent_MAE": parent.get("outcomes", {}).get("mae_percent"),
            "time_parent_to_child_seconds": int((child_time - parent_time).total_seconds()),
            "bars_parent_to_child": int((child_time - parent_time).total_seconds() // 60),
            "peak_price_between": max(prices, default=None), "lowest_price_between": min(lows, default=None),
            "pullback_from_parent_peak_percent": None if not parent_peak else (parent_peak - child_trigger) / parent_peak * 100,
            "pullback_from_parent_peak_R": None,
            "child_entry_vs_parent_trigger_percent": None if not float(parent.get("trigger_price", 0)) else (child_trigger - float(parent["trigger_price"])) / float(parent["trigger_price"]) * 100,
            "child_entry_vs_parent_peak_percent": None if not parent_peak else (child_trigger - parent_peak) / parent_peak * 100,
            "child_MFE": child.get("outcomes", {}).get("mfe_percent"), "child_MAE": child.get("outcomes", {}).get("mae_percent"),
            "child_max_R": child.get("outcomes", {}).get("maximum_R"),
            "child_targets": {str(p): _event(child.get("outcomes", {}), p) for p in (2, 3, 5, 8, 10)},
            "child_feature_context": child.get("features", {}).get("values", {})}


def transition_matrix(transitions: Iterable[dict], *, min_sample: int = 30) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for transition in transitions:
        grouped[(transition.get("parent_primary_strategy"), transition.get("child_primary_strategy"))].append(transition)
    result = []
    for (parent, child), values in sorted(grouped.items()):
        n = len(values)
        result.append({"parent_strategy": parent, "child_strategy": child, "sample_count": n,
                       "confidence_state": "INSUFFICIENT_SAMPLE" if n < min_sample else "EARLY" if n < 100 else "MODERATE" if n < 500 else "LARGE_SAMPLE",
                       "child_target_rates": {str(p): sum(bool(item["child_targets"][str(p)].get("hit")) for item in values) / n for p in (2, 3, 5, 8, 10)},
                       "median_child_mfe": median(float(item["child_MFE"]) for item in values if item.get("child_MFE") is not None) if any(item.get("child_MFE") is not None for item in values) else None,
                       "median_child_mae": median(float(item["child_MAE"]) for item in values if item.get("child_MAE") is not None) if any(item.get("child_MAE") is not None for item in values) else None,
                       "median_time_to_child_seconds": median(item["time_parent_to_child_seconds"] for item in values)})
    return result


def hold_vs_reentry(parent: dict, child: dict, *, parent_target_percent: int = 5) -> dict:
    parent_event = _event(parent.get("outcomes", {}), parent_target_percent)
    hold = parent.get("outcomes", {}).get("horizons", {}).get("3600", {}).get("mfe_percent")
    child_mfe = child.get("outcomes", {}).get("mfe_percent")
    ambiguity = parent_event.get("first_plan_event") == "INTRABAR_ORDER_UNKNOWN"
    return {"research_version": REENTRY_TRANSITION_VERSION, "simulated": True, "not_actual_fill": True,
            "runner_from_parent_target_return": None if ambiguity else hold,
            "child_reentry_return": None if ambiguity else child_mfe,
            "combined_partial_plus_reentry_return": None if ambiguity or child_mfe is None else parent_target_percent / 2 + float(child_mfe) / 2,
            "ambiguity": "INTRABAR_ORDER_UNKNOWN" if ambiguity else None}


def first_tranche_report(rows: Iterable[dict]) -> dict:
    """Neutral, sample-size-aware report preset for the first historical tranche."""
    rows = tuple(rows)
    return {"preset": "FIRST_TRANCHE", "warning": "observational research; no production policy promotion",
            "by_strategy": cohort_report(rows, ("strategy",)),
            "by_dimension": {dimension: cohort_report(rows, (dimension,))
                             for dimension in ("time_of_day_bucket", "gap_bucket", "volume_behavior_bucket",
                                               "pullback_depth_bucket", "extension_bucket", "strategy_combination")}}

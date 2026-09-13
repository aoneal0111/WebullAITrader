"""Offline cohort, chronological split, and research-only simulations."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
import json
import sqlite3
from statistics import median
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable

from .models import ACTIVE_STRATEGIES

PROFIT_RESEARCH_VERSION = "ATLAS_PROFIT_RESEARCH_V1"
REENTRY_TRANSITION_VERSION = "ATLAS_REENTRY_TRANSITIONS_V1"
PERCENT_PARTIAL_TARGETS = (2, 3, 5, 8, 10)
R_TARGETS = (1, 1.5, 2, 3)
PHASE2_R_TARGETS = (0.5, 1, 1.5, 2, 3, 4, 5)


GROUP_DIMENSIONS = (
    "strategy", "primary_strategy", "strategy_combination", "membership_count",
    "time_of_day_bucket", "price_bucket", "gap_bucket", "volume_behavior_bucket",
    "volatility_bucket", "pullback_depth_bucket", "distance_from_vwap_bucket",
    "distance_from_hod_bucket", "extension_bucket", "setup_duration_bucket",
    "provider", "feed", "source_quality",
)


class ReportProgress:
    """Bounded, stderr-only progress reporting for streaming analysis."""

    def __init__(self, *, total: int | None = None, interval: int = 10000, stream=None) -> None:
        self.total = total
        self.interval = max(1, interval)
        self.stream = stream or sys.stderr
        self._phase = None
        self._processed = 0
        self._last_reported = 0
        self._started = time.monotonic()

    def _emit(self, phase: str, processed: int, *, force: bool = False) -> None:
        if not force and processed - self._last_reported < self.interval:
            return
        elapsed = time.monotonic() - self._started
        fields = [f"phase={phase}", f"records_processed={processed}", f"elapsed_seconds={elapsed:.3f}"]
        if self.total is not None:
            fields.extend((f"records_total={self.total}", f"percent_complete={processed / self.total * 100:.2f}" if self.total else "percent_complete=100.00"))
        print("ATLAS_REPORT_PROGRESS " + " ".join(fields), file=self.stream, flush=True)
        self._last_reported = processed

    def begin_phase(self, phase: str) -> None:
        self._phase = phase
        self._processed = 0
        self._last_reported = 0
        self._emit(phase, 0, force=True)

    def advance(self) -> None:
        self._processed += 1
        self._emit(self._phase or "UNKNOWN", self._processed)

    def complete_phase(self) -> None:
        self._emit(self._phase or "UNKNOWN", self._processed, force=True)


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


def _confidence(n: int, min_sample: int) -> str:
    return "INSUFFICIENT_SAMPLE" if n < min_sample else "EARLY" if n < 100 else "MODERATE" if n < 500 else "LARGE_SAMPLE"


def _number(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def streaming_cohort_report(rows: Iterable[dict], group_by: Iterable[str], *, min_sample: int = 30,
                            concentration_threshold: float = .5, progress: ReportProgress | None = None,
                            phase: str = "COHORT") -> list[dict]:
    """Compute cohort results with disk-backed observations and bounded Python memory.

    The source iterator is consumed once. Exact medians are obtained by SQLite
    ordered-offset queries, so the implementation does not retain episode
    dictionaries or metric arrays in Python.
    """
    dimensions = tuple(group_by)
    if any(item not in GROUP_DIMENSIONS for item in dimensions):
        raise ValueError("UNKNOWN_GROUP_DIMENSION")
    handle = tempfile.NamedTemporaryFile(prefix="atlas_report_", suffix=".sqlite3", delete=False)
    db_path = Path(handle.name); handle.close()
    connection = sqlite3.connect(db_path)
    try:
        if progress:
            progress.begin_phase(phase)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("""CREATE TABLE observations (
            group_key TEXT NOT NULL, group_json TEXT NOT NULL, symbol TEXT, trading_date TEXT,
            month TEXT, mfe REAL, mae REAL, maximum_r REAL, reentry INTEGER,
            h2 INTEGER, h3 INTEGER, h5 INTEGER, h8 INTEGER, h10 INTEGER,
            s2 INTEGER, s3 INTEGER, s5 INTEGER, s8 INTEGER, s10 INTEGER,
            t2 REAL, t3 REAL, t5 REAL, t8 REAL, t10 REAL)""")
        insert = "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        batch = []
        for row in rows:
            if progress:
                progress.advance()
            keys = []
            for dimension in dimensions:
                value = _dimension(row, dimension)
                keys.append(value)
            expanded = keys[dimensions.index("strategy")] if "strategy" in dimensions else (None,)
            for strategy in expanded:
                values = list(keys)
                if "strategy" in dimensions:
                    values[dimensions.index("strategy")] = strategy
                group = dict(zip(dimensions, values))
                outcomes = row.get("outcomes", {})
                targets = outcomes.get("percent_targets", {})
                events = [targets.get(str(percent), {}) for percent in (2, 3, 5, 8, 10)]
                batch.append((json.dumps(values, sort_keys=True, default=str), json.dumps(group, sort_keys=True, default=str),
                              row.get("symbol"), row.get("trading_date"), str(row.get("trading_date", ""))[:7],
                              _number(outcomes.get("mfe_percent")), _number(outcomes.get("mae_percent")),
                              _number(outcomes.get("maximum_R")), int(bool(row.get("reentry"))),
                              *[int(bool(event.get("hit"))) for event in events],
                              *[int(event.get("first_plan_event") == "STOP_FIRST") for event in events],
                              *[_number(event.get("elapsed_seconds")) for event in events]))
                if len(batch) >= 2000:
                    connection.executemany(insert, batch); batch.clear()
        if batch:
            connection.executemany(insert, batch)
        connection.commit()
        if progress:
            progress.complete_phase()
        groups = connection.execute("SELECT group_key, group_json, COUNT(*), COUNT(DISTINCT symbol), COUNT(DISTINCT trading_date) FROM observations GROUP BY group_key, group_json ORDER BY group_key").fetchall()
        result = []
        metric_columns = {"mfe": "mfe", "mae": "mae", "maximum_R": "maximum_r"}
        target_columns = {2: ("h2", "s2", "t2"), 3: ("h3", "s3", "t3"), 5: ("h5", "s5", "t5"), 8: ("h8", "s8", "t8"), 10: ("h10", "s10", "t10")}
        for group_key, group_json, n, unique_symbols, unique_dates in groups:
            def median_metric(column):
                count = connection.execute(f"SELECT COUNT({column}) FROM observations WHERE group_key=?", (group_key,)).fetchone()[0]
                if not count: return None
                values = connection.execute(f"SELECT {column} FROM observations WHERE group_key=? AND {column} IS NOT NULL ORDER BY {column}", (group_key,)).fetchall()
                middle = (count - 1) // 2
                if count % 2: return values[middle][0]
                return (values[middle][0] + values[middle + 1][0]) / 2
            def median_target(column):
                values = [item[0] for item in connection.execute(f"SELECT {column} FROM observations WHERE group_key=? AND {column} IS NOT NULL ORDER BY {column}", (group_key,))]
                return None if not values else values[(len(values) - 1) // 2] if len(values) % 2 else (values[len(values)//2-1] + values[len(values)//2]) / 2
            concentration = {}
            for field, alias in (("symbol", "symbol"), ("trading_date", "date"), ("month", "month")):
                concentration[alias] = connection.execute(f"SELECT MAX(n) FROM (SELECT {field}, COUNT(*) n FROM observations WHERE group_key=? GROUP BY {field})", (group_key,)).fetchone()[0] or 0
            largest = {key: value / n if n else 0 for key, value in concentration.items()}
            flags = []
            if largest["symbol"] >= concentration_threshold: flags.append("SYMBOL_CONCENTRATED")
            if largest["date"] >= concentration_threshold: flags.append("DATE_CONCENTRATED")
            if largest["month"] >= concentration_threshold: flags.append("TEMPORALLY_CONCENTRATED")
            target_rates = {}; stop_rates = {}; times = {}
            for percent, (hit, stop, elapsed) in target_columns.items():
                target_rates[str(percent)] = connection.execute(f"SELECT AVG({hit}) FROM observations WHERE group_key=?", (group_key,)).fetchone()[0] or 0
                stop_rates[str(percent)] = connection.execute(f"SELECT AVG({stop}) FROM observations WHERE group_key=?", (group_key,)).fetchone()[0] or 0
                times[str(percent)] = median_target(elapsed)
            result.append({"group": json.loads(group_json), "sample_count": n, "confidence_state": _confidence(n, min_sample),
                           "unique_symbols": unique_symbols, "unique_dates": unique_dates,
                           "median_mfe": median_metric("mfe"), "median_mae": median_metric("mae"),
                           "median_maximum_r": median_metric("maximum_r"), "target_hit_rates": target_rates,
                           "stop_first_rates": stop_rates, "median_time_to_target_seconds": times,
                           "reentry_frequency": connection.execute("SELECT AVG(reentry) FROM observations WHERE group_key=?", (group_key,)).fetchone()[0] or 0,
                           "largest_symbol_share": largest["symbol"], "largest_date_share": largest["date"],
                           "largest_month_share": largest["month"], "concentration_flags": flags})
        return result
    finally:
        connection.close()
        try: db_path.unlink()
        except OSError: pass


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


def first_tranche_report_streaming(row_factory, *, total: int | None = None,
                                   progress: ReportProgress | None = None) -> dict:
    """Streaming equivalent of :func:`first_tranche_report` for large corpora."""
    dimensions = ("time_of_day_bucket", "gap_bucket", "volume_behavior_bucket",
                  "pullback_depth_bucket", "extension_bucket", "strategy_combination")
    if progress:
        progress.begin_phase("START")
        progress.complete_phase()
    result = {"preset": "FIRST_TRANCHE", "warning": "observational research; no production policy promotion",
              "by_strategy": streaming_cohort_report(row_factory(), ("strategy",), progress=progress,
                                                       phase="BY_STRATEGY"),
              "by_dimension": {dimension: streaming_cohort_report(row_factory(), (dimension,), progress=progress,
                                                                    phase=dimension.upper()) for dimension in dimensions}}
    if progress:
        progress.begin_phase("FINALIZE")
        progress.complete_phase()
        progress.begin_phase("COMPLETE")
        progress.complete_phase()
    return result


def _volume_context_bucket(row: dict) -> str | None:
    """Derive a conservative volume context from persisted nested features."""
    volume = _values(row).get("volume", {})
    current = _number(volume.get("current"))
    baseline = _number(volume.get("rolling_10_mean"))
    if current is None or baseline in (None, 0):
        return None
    ratio = current / baseline
    if ratio < 0.8:
        return "CONTRACTION"
    if ratio > 1.2:
        return "EXPANSION"
    return "NEUTRAL"


def _research_observations(row: dict):
    """Yield compact strategy observations; no episode dictionary is retained."""
    outcomes = row.get("outcomes", {})
    targets = outcomes.get("percent_targets", {})
    events = [targets.get(str(p), {}) for p in PERCENT_PARTIAL_TARGETS]
    r_targets = outcomes.get("r_targets", {})
    r_events = [r_targets.get(str(target), {}) for target in PHASE2_R_TARGETS]
    values = _values(row)
    extension = values.get("extension", {})
    compact = (
        row.get("episode_id"), row.get("symbol"), row.get("trading_date"),
        values.get("time_of_day_bucket"), _dimension(row, "extension_bucket"),
        _volume_context_bucket(row), _number(outcomes.get("mfe_percent")),
        _number(outcomes.get("mae_percent")), _number(outcomes.get("maximum_R")),
        *[int(bool(event.get("hit"))) for event in events],
        *[int(event.get("first_plan_event") == "STOP_FIRST") for event in events],
        *[_number(event.get("elapsed_seconds")) for event in events],
        *[int(bool(event.get("hit"))) for event in r_events],
        *[int(event.get("first_plan_event") == "STOP_FIRST") for event in r_events],
        *[_number(event.get("elapsed_seconds")) for event in r_events],
        _number(outcomes.get("horizons", {}).get("3600", {}).get("mfe_percent")),
        _number(outcomes.get("post_8", {}).get("mfe_after_target", outcomes.get("post_8", {}).get("maximum_giveback_after_8"))),
        _number(outcomes.get("post_8", {}).get("maximum_giveback_after_8")),
        _number(row.get("trigger_price")), _number(row.get("structural_stop")),
        *[int(event.get("first_plan_event") == "INTRABAR_ORDER_UNKNOWN") for event in events],
    )
    for strategy in row.get("strategy_memberships", ()):
        yield (compact[0], compact[1], compact[2], strategy, *compact[3:])


def _sql_median(connection, column: str, where: str, params: tuple = ()):
    count = connection.execute(
        f"SELECT COUNT({column}) FROM observations WHERE {where} AND {column} IS NOT NULL", params
    ).fetchone()[0]
    if not count:
        return None
    values = connection.execute(
        f"SELECT {column} FROM observations WHERE {where} AND {column} IS NOT NULL ORDER BY {column}", params
    ).fetchall()
    middle = (count - 1) // 2
    if count % 2:
        return values[middle][0]
    return (values[middle][0] + values[middle + 1][0]) / 2


def _sql_metrics(connection, where: str = "1=1", params: tuple = ()) -> dict:
    count, symbols, dates = connection.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT symbol), COUNT(DISTINCT trading_date) "
        f"FROM observations WHERE {where}", params
    ).fetchone()
    result = {
        "sample_count": count, "unique_symbols": symbols, "unique_dates": dates,
        "median_mfe": _sql_median(connection, "mfe", where, params),
        "median_mae": _sql_median(connection, "mae", where, params),
        "median_maximum_r": _sql_median(connection, "maximum_r", where, params),
        "target_hit_rates": {}, "stop_first_rates": {},
        "median_time_to_target_seconds": {},
        "confidence_state": _confidence(count, 30),
        "concentration": {},
    }
    for percent in PERCENT_PARTIAL_TARGETS:
        hit, stop = {2: ("h2", "s2"), 3: ("h3", "s3"), 5: ("h5", "s5"),
                     8: ("h8", "s8"), 10: ("h10", "s10")}[percent]
        hit_value, stop_value = connection.execute(
            f"SELECT AVG({hit}), AVG({stop}) FROM observations WHERE {where}", params
        ).fetchone()
        result["target_hit_rates"][str(percent)] = hit_value
        result["stop_first_rates"][str(percent)] = stop_value
        result["median_time_to_target_seconds"][str(percent)] = _sql_median(
            connection, f"t{percent}", where, params
        )
    for label, column in (("symbol", "symbol"), ("date", "trading_date"), ("month", "substr(trading_date,1,7)")):
        top = connection.execute(
            f"SELECT {column}, COUNT(*) FROM observations WHERE {where} GROUP BY {column} "
            "ORDER BY COUNT(*) DESC LIMIT 1", params
        ).fetchone()
        result["concentration"][f"largest_{label}_share"] = (top[1] / count) if top and count else 0
    return result


def _research_groups(connection, column: str) -> list[dict]:
    groups = connection.execute(
        f"SELECT {column}, COUNT(*) FROM observations WHERE {column} IS NOT NULL GROUP BY {column} ORDER BY {column}"
    ).fetchall()
    return [{"group": value, **_sql_metrics(connection, f"{column} = ?", (value,))} for value, _ in groups]


def _policy_columns(kind: str, target) -> tuple[str, str, str, str]:
    if kind == "percent":
        suffix = str(int(target))
        return f"h{suffix}", f"s{suffix}", f"t{suffix}", f"a{suffix}"
    suffix = str(target).replace(".", "")
    return f"rh{suffix}", f"rs{suffix}", f"rt{suffix}", "0"


def _sql_policy_metrics(connection, where: str, params: tuple, *, kind: str, target,
                        partial_percent: int | None = None) -> dict:
    hit, stop, elapsed, ambiguity = _policy_columns(kind, target)
    count = connection.execute(f"SELECT COUNT(*) FROM observations WHERE {where}", params).fetchone()[0]
    if kind == "percent":
        hit_expr, stop_expr, time_expr = hit, stop, elapsed
        ambiguity_expr = ambiguity
    else:
        hit_expr, stop_expr, time_expr = hit, stop, elapsed
        ambiguity_expr = "0"
    values = {
        "sample_count": count,
        "hit_rate": connection.execute(f"SELECT AVG({hit_expr}) FROM observations WHERE {where}", params).fetchone()[0] if count else None,
        "stop_first_rate": connection.execute(f"SELECT AVG({stop_expr}) FROM observations WHERE {where}", params).fetchone()[0] if count else None,
        "median_time_to_target_seconds": _sql_median(connection, time_expr, where, params),
        "median_mfe": _sql_median(connection, "mfe", where, params),
        "median_mae": _sql_median(connection, "mae", where, params),
        "median_maximum_r": _sql_median(connection, "maximum_r", where, params),
        "ambiguity_count": connection.execute(f"SELECT SUM({ambiguity_expr}) FROM observations WHERE {where}", params).fetchone()[0] or 0,
        "confidence_state": _confidence(count, 30),
    }
    return values


def _sql_policy_value_metrics(connection, where: str, params: tuple, *, kind: str, target,
                              partial_percent: int) -> dict:
    hit, _stop, _elapsed, ambiguity = _policy_columns(kind, target)
    target_return = float(target) if kind == "percent" else None
    target_expr = str(target_return) if target_return is not None else str(float(target))
    ambiguous = ambiguity if kind == "percent" else "0"
    resolved = f"({ambiguous} = 0 AND ({hit} = 1 OR {partial_percent} = 0))"
    runner = "horizon_mfe"
    realized = f"CASE WHEN {resolved} THEN {partial_percent / 100:g} * {target_expr} END"
    total = (f"CASE WHEN {ambiguous} = 1 THEN NULL "
             f"WHEN {realized} IS NULL AND {runner} IS NULL THEN NULL "
             f"ELSE COALESCE({realized}, 0) + {(100 - partial_percent) / 100:g} * COALESCE({runner}, 0) END")
    count = connection.execute(f"SELECT COUNT(*) FROM observations WHERE {where}", params).fetchone()[0]
    usable = f"{total} IS NOT NULL"
    median_return = _sql_median(connection, total, f"{where} AND {usable}", params)
    mean_return, positive_rate, tail = connection.execute(
        f"SELECT AVG({total}), AVG(CASE WHEN {total} > 0 THEN 1.0 ELSE 0.0 END), "
        f"MAX({total}) FROM observations WHERE {where}", params
    ).fetchone() if count else (None, None, None)
    return {"sample_count": count, "gross_return_percent_mean": mean_return,
            "median_return_percent": median_return, "positive_return_rate": positive_rate,
            "tail_contribution_max_percent": tail, "partial_percent": partial_percent,
            "target": target, "target_kind": kind, "simulation": "SIMULATED",
            "fill_status": "NO_SLIPPAGE_CLAIM_NO_REAL_FILL_CLAIM",
            "ambiguity_count": connection.execute(f"SELECT SUM({ambiguous}) FROM observations WHERE {where}", params).fetchone()[0] or 0,
            "confidence_state": _confidence(count, 30)}


def _sql_policy_set(connection, strategy: str | None = None, where: str = "1=1",
                    params: tuple = ()) -> dict:
    prefix = f"strategy = ? AND {where}" if strategy is not None else where
    query_params = (strategy, *params) if strategy is not None else params
    percent = {str(target): _sql_policy_metrics(connection, prefix, query_params, kind="percent", target=target)
               for target in PERCENT_PARTIAL_TARGETS}
    r_targets = {str(target): _sql_policy_metrics(connection, prefix, query_params, kind="r", target=target)
                 for target in PHASE2_R_TARGETS}
    partial = {str(target): {str(part): _sql_policy_value_metrics(connection, prefix, query_params, kind="percent", target=target, partial_percent=part)
                             for part in (0, 25, 50, 75, 100)} for target in PERCENT_PARTIAL_TARGETS}
    return {"percent_targets": percent, "r_targets": r_targets, "partial_exit_policies": partial}


def _sql_policy_quick(connection, where: str, params: tuple) -> dict:
    count, h5, h8, rh2 = connection.execute(
        f"SELECT COUNT(*), AVG(h5), AVG(h8), AVG(rh2) FROM observations WHERE {where}", params
    ).fetchone()
    return {"sample_count": count, "5pct_hit_rate": h5, "8pct_hit_rate": h8,
            "2R_hit_rate": rh2, "confidence_state": _confidence(count, 30)}


def _policy_stability(connection, strategy: str, train_end: str | None,
                      validation_end: str | None) -> dict:
    split_metrics = {}
    for name, actual in (("TRAIN", ("trading_date <= ?", (train_end,))),
                         ("VALIDATION", ("trading_date > ? AND trading_date <= ?", (train_end, validation_end))),
                         ("TEST", ("trading_date > ?", (validation_end,)))):
        where, params = actual
        split_metrics[name] = _sql_policy_set(connection, strategy, *actual)
    return split_metrics


def _policy_stability_summary(connection, strategy: str, train_end: str | None,
                              validation_end: str | None) -> dict:
    ranges = (("TRAIN", "trading_date <= ?", (train_end,)),
              ("VALIDATION", "trading_date > ? AND trading_date <= ?", (train_end, validation_end)),
              ("TEST", "trading_date > ?", (validation_end,)))
    output = {}
    for label, where, params in ranges:
        output[label] = {"5pct": _sql_policy_metrics(connection, "strategy = ? AND " + where, (strategy, *params), kind="percent", target=5),
                         "8pct": _sql_policy_metrics(connection, "strategy = ? AND " + where, (strategy, *params), kind="percent", target=8),
                         "2R": _sql_policy_metrics(connection, "strategy = ? AND " + where, (strategy, *params), kind="r", target=2)}
    rates = [output[name]["8pct"]["hit_rate"] for name in ("TRAIN", "VALIDATION", "TEST") if output[name]["8pct"]["hit_rate"] is not None]
    output["descriptive_status"] = ("INSUFFICIENT_SAMPLE" if any(output[name]["8pct"]["confidence_state"] == "INSUFFICIENT_SAMPLE" for name in output if name in ("TRAIN", "VALIDATION", "TEST"))
                                     else "STABLE" if len(rates) == 3 and max(rates) - min(rates) <= .1
                                     else "DEGRADING" if len(rates) == 3 and rates[-1] < rates[0]
                                     else "IMPROVING" if len(rates) == 3 and rates[-1] > rates[0]
                                     else "INCONSISTENT")
    return output


def full_research_report_streaming(row_factory, *, total: int | None = None,
                                   progress: ReportProgress | None = None) -> dict:
    """Full-research report using one streaming ingest and SQL aggregates."""
    handle = tempfile.NamedTemporaryFile(prefix="atlas_full_research_", suffix=".sqlite3", delete=False)
    db_path = Path(handle.name); handle.close()
    connection = sqlite3.connect(db_path)
    try:
        if progress:
            progress.begin_phase("START"); progress.complete_phase(); progress.begin_phase("INGEST")
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        columns = ["episode_id", "symbol", "trading_date", "strategy", "time_of_day", "extension_bucket", "volume_bucket",
                   "mfe", "mae", "maximum_r"]
        columns += [f"h{p}" for p in (2, 3, 5, 8, 10)]
        columns += [f"s{p}" for p in (2, 3, 5, 8, 10)]
        columns += [f"t{p}" for p in (2, 3, 5, 8, 10)]
        columns += [f"rh{str(p).replace('.', '')}" for p in PHASE2_R_TARGETS]
        columns += [f"rs{str(p).replace('.', '')}" for p in PHASE2_R_TARGETS]
        columns += [f"rt{str(p).replace('.', '')}" for p in PHASE2_R_TARGETS]
        columns += ["horizon_mfe", "post8_mfe", "post8_giveback", "trigger_price", "structural_stop"]
        columns += [f"a{p}" for p in (2, 3, 5, 8, 10)]
        types = {name: "REAL" for name in columns}
        types.update({name: "TEXT" for name in ("episode_id", "symbol", "trading_date", "strategy", "time_of_day", "extension_bucket", "volume_bucket")})
        connection.execute("CREATE TABLE observations (" + ", ".join(f"{name} {types[name]}" for name in columns) + ")")
        connection.execute("CREATE INDEX observations_strategy ON observations(strategy)")
        connection.execute("CREATE INDEX observations_date ON observations(trading_date)")
        insert = "INSERT INTO observations VALUES (" + ",".join("?" for _ in columns) + ")"
        batch = []
        for row in row_factory():
            batch.extend(_research_observations(row))
            if progress:
                progress.advance()
            if len(batch) >= 2000:
                connection.executemany(insert, batch); batch.clear()
        if batch:
            connection.executemany(insert, batch)
        connection.commit()
        if progress: progress.complete_phase(); progress.begin_phase("TEMPORAL_BOUNDARIES")
        dates = [item[0] for item in connection.execute("SELECT DISTINCT trading_date FROM observations ORDER BY trading_date")]
        if dates:
            train_end = dates[max(0, int(len(dates) * .6) - 1)]
            validation_end = dates[max(0, int(len(dates) * .8) - 1)]
        else:
            train_end = validation_end = None
        temporal = {"method": "strict trading-date chronology", "random_split": False,
                    "train_end": train_end, "validation_end": validation_end,
                    "test_start": (validation_end if validation_end is None else dates[dates.index(validation_end) + 1]) if dates and validation_end != dates[-1] else None,
                    "splits": {}}
        if progress: progress.complete_phase()
        boundaries = (("TRAIN", "trading_date <= ?", (train_end,)),
                      ("VALIDATION", "trading_date > ? AND trading_date <= ?", (train_end, validation_end)),
                      ("TEST", "trading_date > ?", (validation_end,)))
        for name, where, params in boundaries:
            if progress: progress.begin_phase(name)
            temporal["splits"][name] = {strategy: _sql_metrics(connection, f"strategy = ? AND {where}", (strategy, *params))
                                        for strategy in ACTIVE_STRATEGIES}
            if progress: progress.advance(); progress.complete_phase()
        policy_stability = {strategy: _policy_stability_summary(connection, strategy, train_end, validation_end)
                            for strategy in ACTIVE_STRATEGIES}
        temporal["policy_stability"] = policy_stability
        if progress: progress.begin_phase("STRATEGY_SCORECARDS")
        scorecards = {strategy: _sql_metrics(connection, "strategy = ?", (strategy,)) for strategy in ACTIVE_STRATEGIES}
        if progress: progress.advance(); progress.complete_phase()
        if progress: progress.begin_phase("PROFIT_RESEARCH")
        profit = {strategy: _sql_policy_set(connection, strategy) for strategy in ACTIVE_STRATEGIES}
        if progress: progress.advance(); progress.complete_phase()
        if progress: progress.begin_phase("R_MULTIPLE_RESEARCH")
        r_multiple = {strategy: profit[strategy]["r_targets"] for strategy in ACTIVE_STRATEGIES}
        if progress: progress.advance(); progress.complete_phase()
        if progress: progress.begin_phase("CAPITAL_SCENARIOS")
        capital = {}
        constant_risk = {}
        for strategy in ACTIVE_STRATEGIES:
            capital[strategy] = {target: capital_scenarios(profit[strategy]["partial_exit_policies"][target]["100"]["gross_return_percent_mean"])
                                 for target in map(str, PERCENT_PARTIAL_TARGETS)}
            valid_geometry, excluded_geometry = connection.execute(
                "SELECT COUNT(*), SUM(CASE WHEN trigger_price IS NULL OR structural_stop IS NULL OR trigger_price <= structural_stop THEN 1 ELSE 0 END) "
                "FROM observations WHERE strategy = ?", (strategy,)).fetchone()
            excluded_geometry = excluded_geometry or 0
            sizes = {}
            for budget in (25, 50, 100, 200, 500):
                average_shares = connection.execute(
                    "SELECT AVG(? / (trigger_price - structural_stop)) FROM observations "
                    "WHERE strategy = ? AND trigger_price IS NOT NULL AND structural_stop IS NOT NULL "
                    "AND trigger_price > structural_stop", (budget, strategy)).fetchone()[0]
                sizes[str(budget)] = {"risk_budget": budget, "valid_observations": valid_geometry - excluded_geometry,
                                      "excluded_invalid_geometry": excluded_geometry, "mean_theoretical_shares": average_shares,
                                      "simulation": "THEORETICAL"}
            constant_risk[strategy] = sizes
        if progress: progress.advance(); progress.complete_phase()
        if progress: progress.begin_phase("WALK_FORWARD")
        folds = []
        walk_policy_summaries = []
        if len(dates) >= 4:
            train_days, test_days, step = 30, 10, 10
            for start in range(train_days, len(dates) - test_days + 1, step):
                train_start, train_end_fold = dates[start - train_days], dates[start - 1]
                test_start, test_end = dates[start], dates[start + test_days - 1]
                walk_policy_summaries.append({"fold": len(folds) + 1, "strategies": {
                    strategy: {"train": _sql_policy_quick(connection, "strategy = ? AND trading_date BETWEEN ? AND ?", (strategy, train_start, train_end_fold)),
                               "test": _sql_policy_quick(connection, "strategy = ? AND trading_date BETWEEN ? AND ?", (strategy, test_start, test_end))}
                    for strategy in ACTIVE_STRATEGIES}})
                folds.append({"fold": len(folds) + 1, "train_date_range": [train_start, train_end_fold],
                              "test_date_range": [test_start, test_end], "strategies": {
                                  strategy: {"train": _sql_metrics(connection, "strategy = ? AND trading_date BETWEEN ? AND ?", (strategy, train_start, train_end_fold)),
                                             "test": _sql_metrics(connection, "strategy = ? AND trading_date BETWEEN ? AND ?", (strategy, test_start, test_end))}
                                  for strategy in ACTIVE_STRATEGIES}})
        if progress: progress.advance(); progress.complete_phase()
        if progress: progress.begin_phase("CONTEXT")
        context = {
            "time_of_day": _research_groups(connection, "time_of_day"),
            "extension": _research_groups(connection, "extension_bucket"),
            "volume": {"status": "AVAILABLE", "source_fields": ["features.values.volume.current", "features.values.volume.rolling_10_mean"],
                        "bucket_rule": "ratio < 0.8 CONTRACTION; ratio > 1.2 EXPANSION; otherwise NEUTRAL",
                        "buckets": _research_groups(connection, "volume_bucket")},
            "gap": {"status": "UNAVAILABLE_FROM_PERSISTED_EPISODE_FIELD"},
            "pullback": {"status": "UNAVAILABLE_NULL_DOMINATED"},
        }
        if progress: progress.begin_phase("RUNNER_RESEARCH")
        runner = {}
        for strategy in ACTIVE_STRATEGIES:
            where, params = "strategy = ? AND a8 = 0", (strategy,)
            runner[strategy] = {"sample_count": connection.execute(f"SELECT COUNT(*) FROM observations WHERE {where} AND horizon_mfe IS NOT NULL", params).fetchone()[0],
                                "median_return_percent": _sql_median(connection, "horizon_mfe", where, params),
                                "positive_return_rate": connection.execute(f"SELECT AVG(CASE WHEN horizon_mfe > 0 THEN 1.0 ELSE 0.0 END) FROM observations WHERE {where} AND horizon_mfe IS NOT NULL", params).fetchone()[0],
                                "post_8_mfe_median": _sql_median(connection, "post8_mfe", where, params),
                                "giveback_median": _sql_median(connection, "post8_giveback", where, params),
                                "terminal_reason_frequencies": {"SESSION_CLOSE": connection.execute(f"SELECT COUNT(*) FROM observations WHERE {where}", params).fetchone()[0]}}
        if progress: progress.advance(); progress.complete_phase(); progress.begin_phase("FINALIZE"); progress.complete_phase(); progress.begin_phase("COMPLETE"); progress.complete_phase()
        return {
            "preset": "FULL_RESEARCH", "metadata": {"phase": 1, "research_only": True, "records_ingested": len(dates) and connection.execute("SELECT COUNT(DISTINCT episode_id) FROM observations").fetchone()[0] or 0,
                "temporal_method": "strict trading-date chronology", "no_random_split": True},
            "limitations": ["ALPACA IEX single-exchange research", "not point-in-time universe", "no SIP", "one failed acquisition partition", "gap unavailable from persisted episode field", "pullback null-dominated"],
            "capabilities": {"profit_research": "IMPLEMENTED", "r_multiple_research": "IMPLEMENTED", "partial_exit_research": "IMPLEMENTED", "runner_research": "IMPLEMENTED", "capital_scenarios": "IMPLEMENTED", "constant_risk_scenarios": "IMPLEMENTED", "reentry": "NOT_YET_IMPLEMENTED", "failure_analysis": "NOT_YET_IMPLEMENTED"},
            "strategy_scorecards": scorecards, "temporal": temporal, "walk_forward": {"folds": folds, "policy_summary": walk_policy_summaries, "number_of_folds": len(folds)},
            "context_analysis": context, "profit_research": profit, "r_multiple_research": r_multiple,
            "partial_exit_research": {strategy: profit[strategy]["partial_exit_policies"] for strategy in ACTIVE_STRATEGIES},
            "runner_research": runner, "capital_scenarios": capital, "constant_risk_scenarios": constant_risk,
        }
    finally:
        connection.close()
        try: db_path.unlink()
        except OSError: pass

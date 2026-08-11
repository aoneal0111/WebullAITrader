"""Run frozen-configuration V1 characterization over the captured dataset."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
import csv
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, ScannerObservation,
)
from app.momentum_scanner.rules import evaluate_candidate

from .configuration import WarriorMomentumConfig
from .models import CandidateStatus, SetupState
from .runtime import WarriorMomentumRuntime
from .validation import (
    BASELINE, CONSERVATIVE, IDEALIZED, float_bucket,
    price_bucket, rvol_bucket, score_bucket,
)
from .validation_dataset import load_dataset

NEW_YORK = ZoneInfo("America/New_York")
ZERO = Decimal("0")


def run(dataset_directory: Path, output_directory: Path) -> dict[str, object]:
    dataset = load_dataset(dataset_directory)
    config = WarriorMomentumConfig()
    runtime = WarriorMomentumRuntime(config)
    references = {(item.symbol, item.session_date): item for item in dataset.references}
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for bar in dataset.bars:
        grouped[(bar.symbol, bar.timestamp.astimezone(NEW_YORK).date().isoformat())].append(bar)
    eligible_keys = tuple(sorted(set(grouped) & set(references), key=lambda item: (item[1], item[0])))

    # Cross-sectional top-gapper membership uses only each completed bar and
    # its already-known previous close.
    changes_by_time: dict[object, list[tuple[Decimal, str]]] = defaultdict(list)
    for (symbol, session_date), bars in grouped.items():
        reference = references.get((symbol, session_date))
        if reference is None:
            continue
        for bar in bars:
            changes_by_time[bar.timestamp + timedelta(minutes=1)].append(
                ((bar.close - reference.previous_close) / reference.previous_close * 100, symbol)
            )
    top_gappers = {
        timestamp: frozenset(symbol for change, symbol in sorted(values, key=lambda item: (-item[0], item[1]))[:config.top_gapper_count] if change > 0)
        for timestamp, values in changes_by_time.items()
    }

    peak_candidates = []
    in_play_sessions: set[tuple[str, str]] = set()
    near_sessions: set[tuple[str, str]] = set()
    qualified_sessions: set[tuple[str, str]] = set()
    setup_sessions: set[tuple[str, str, str]] = set()
    triggered_sessions: set[tuple[str, str, str]] = set()
    stop_models: dict[str, list[Decimal]] = defaultdict(list)
    large_risk_rejections: Counter[str] = Counter()
    current_atlas_qualified: set[tuple[str, str]] = set()
    failure_modes: Counter[str] = Counter()

    for key in sorted(grouped, key=lambda item: (item[1], item[0])):
        symbol, session_date = key
        reference = references.get(key)
        if reference is None:
            continue
        bars = tuple(sorted(grouped[key], key=lambda item: item.timestamp))
        cumulative_volume = ZERO
        session_candidates = []
        prior_setup_state = None
        for index, bar in enumerate(bars):
            cumulative_volume += bar.volume
            decision_time = bar.timestamp + timedelta(minutes=1)
            observation = ScannerObservation(
                symbol=symbol, timestamp=decision_time, price=bar.close,
                previous_close=reference.previous_close,
                current_volume=cumulative_volume,
                average_30_day_volume=reference.average_prior_30_day_volume,
                float_shares=None, bid=None, ask=None,
                catalyst=CatalystType.NONE, catalyst_headline=None,
                tradable=True, halted=False, asset_class=AssetClass.STOCK,
                catalyst_status=CatalystStatus.UNAVAILABLE,
            )
            candidate = runtime.discover(
                observation, bars[: index + 1], session=_session(bar.timestamp),
                top_gapper=symbol in top_gappers.get(decision_time, ()),
            )
            assessed, signal = runtime.assess_entry(candidate)
            session_candidates.append(assessed)
            if assessed.stocks_in_play:
                in_play_sessions.add(key)
            if assessed.score.total >= config.discovery.near_qualified_score:
                near_sessions.add(key)
            if assessed.score.total >= config.discovery.qualified_score:
                qualified_sessions.add(key)
            setup = assessed.setup
            if setup is not None and setup.state in {SetupState.FORMING, SetupState.TRIGGERED}:
                setup_key = (symbol, session_date, setup.setup_type.value)
                setup_sessions.add(setup_key)
                if setup.state is SetupState.TRIGGERED:
                    triggered_sessions.add(setup_key)
                    if setup.trigger is not None and setup.stop_price is not None and setup.stop_model is not None:
                        risk = setup.trigger - setup.stop_price
                        stop_models[setup.stop_model.value].append(risk)
                        if risk > config.entry.maximum_risk_per_share:
                            large_risk_rejections[setup.stop_model.value] += 1
            if signal is not None:
                raise AssertionError("missing historical spread/catalyst evidence unexpectedly produced a signal")
            current = evaluate_candidate(observation)
            if current.qualified:
                current_atlas_qualified.add(key)
        if session_candidates:
            peak = max(session_candidates, key=lambda item: (item.score.total, item.timestamp))
            peak_candidates.append(peak)
            failure_modes.update(code.value for code in peak.reason_codes)
            if peak.setup is None or peak.setup.state is SetupState.NOT_FORMED:
                failure_modes["NO_SETUP_OR_NOT_FORMED"] += 1

    scenarios = {}
    for scenario in (IDEALIZED, BASELINE, CONSERVATIVE):
        scenarios[scenario.name.value] = {
            "sample_size": 0, "wins": 0, "losses": 0, "scratches": 0,
            "win_rate": None, "loss_rate": None, "scratch_rate": None,
            "profit_factor": None, "expectancy_r": None, "average_r": None,
            "median_r": None, "total_r": None, "maximum_drawdown_r": None,
            "average_mae_r": None, "average_mfe_r": None,
            "average_hold_seconds": None, "median_hold_seconds": None,
            "largest_win_r": None, "largest_loss_r": None,
            "maximum_consecutive_wins": 0, "maximum_consecutive_losses": 0,
        }

    report = {
        "dataset": {
            "dataset_id": dataset.dataset_id, "sha256": dataset.sha256,
            "source": dataset.source, "selection_method": dataset.selection_method,
            "raw_date_start": min(item[1] for item in grouped),
            "raw_date_end": max(item[1] for item in grouped),
            "evaluated_date_start": min(item[1] for item in eligible_keys),
            "evaluated_date_end": max(item[1] for item in eligible_keys),
            "symbols": sorted({item[0] for item in grouped}),
            "symbol_count": len({item[0] for item in grouped}),
            "raw_symbol_sessions": len(grouped),
            "evaluated_symbol_sessions": len(eligible_keys), "bars": len(dataset.bars),
            "evidence": {
                "catalyst": dataset.catalyst_evidence, "spread": dataset.spread_evidence,
                "float": dataset.float_evidence, "halt": dataset.halt_evidence,
                "tradability": dataset.tradability_evidence,
            },
        },
        "counts": {
            "discovered_stocks": len(peak_candidates),
            "stocks_in_play": len(in_play_sessions),
            "near_qualified": len(near_sessions),
            "qualified": len(qualified_sessions),
            "setups_detected": len(setup_sessions),
            "setups_triggered": len(triggered_sessions),
            "entry_ready_signals": 0, "paper_trades": 0,
        },
        "performance": scenarios,
        "breakdowns": {
            "setup": _setup_breakdown(setup_sessions, triggered_sessions),
            "score": _candidate_breakdown(peak_candidates, lambda item: score_bucket(item.score.total),
                                          ("<25", "25-44", "45-59", "60-69", "70-79", "80-89", "90-100")),
            "rvol": _candidate_breakdown(peak_candidates, lambda item: rvol_bucket(item.relative_volume),
                                         ("<2x", "2-5x", "5-10x", "10-25x", "25x+")),
            "float": _candidate_breakdown(peak_candidates, lambda item: float_bucket(item.float_shares),
                                          ("<=5M", "5-10M", "10-20M", "20-50M", ">50M", "UNKNOWN")),
            "price": _candidate_breakdown(peak_candidates, lambda item: price_bucket(item.price),
                                          ("<$1", "$1-$2", "$2-$5", "$5-$10", "$10-$20", ">$20")),
            "catalyst": _candidate_breakdown(peak_candidates, lambda item: item.catalyst_status.value,
                                             ("TRUE", "FALSE", "UNKNOWN", "UNAVAILABLE")),
            "session": _candidate_breakdown(peak_candidates, lambda item: item.session,
                                            ("PREMARKET", "REGULAR", "AFTER_HOURS")),
        },
        "stop_analysis": {
            name: {"triggered": len(values),
                   "average_risk_distance": str(sum(values, ZERO) / len(values)),
                   "large_risk_rejections": large_risk_rejections[name],
                   "stop_out_frequency": None, "mae_before_winner": None}
            for name, values in sorted(stop_models.items())
        },
        "execution_sensitivity": {
            "status": "NOT_MEASURABLE_NO_ENTRY_READY_SIGNALS",
            "break_even_slippage": None,
            "spread": {"0.00": None, "0.50": None, "1.00": None},
            "slippage_per_share": {"0.00": None, "0.01": None, "0.03": None},
            "entry_delay_bars": {"0": None, "1": None},
        },
        "current_atlas_comparison": {
            "coverage_comparable": True,
            "evaluated_symbol_sessions": len(eligible_keys),
            "CURRENT_ATLAS": {"candidates": len(current_atlas_qualified), "trades": 0,
                              "win_rate": None, "profit_factor": None, "expectancy_r": None,
                              "maximum_drawdown_r": None, "average_risk": None, "turnover": None},
            "WARRIOR_MOMENTUM_V1": {"candidates": len(qualified_sessions), "trades": 0,
                                    "win_rate": None, "profit_factor": None, "expectancy_r": None,
                                    "maximum_drawdown_r": None, "average_risk": None, "turnover": None},
            "conclusion": "Outcome comparison unavailable because neither exact strategy produced evidence-complete trades.",
        },
        "daily_risk": {"trades": 0, "maximum_daily_loss_r": None,
                       "maximum_consecutive_losses": 0, "maximum_trades_per_day": 0,
                       "bad_period_concentration": None},
        "failure_modes": dict(failure_modes.most_common()),
        "lookahead_controls": [
            "bar timestamps are interval opens; decisions occur one minute later",
            "features receive only bars completed at decision time",
            "previous close and average volume use prior completed daily bars",
            "cross-sectional gap rank uses only the just-completed bar",
            "entry/exit simulation accepts only bars opening at or after signal time",
            "same-bar stop/target ambiguity is stop-first",
        ],
        "conclusion": "EXPECTANCY_INDETERMINATE_MISSING_EXECUTION_AND_CATALYST_EVIDENCE",
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_empty_ledger(output_directory / "trade_ledger.csv")
    return report


def _session(timestamp) -> str:
    local = timestamp.astimezone(NEW_YORK).time()
    if local < local.replace(hour=9, minute=30, second=0, microsecond=0):
        return "PREMARKET"
    if local < local.replace(hour=16, minute=0, second=0, microsecond=0):
        return "REGULAR"
    return "AFTER_HOURS"


def _candidate_breakdown(candidates, key, required=()):
    counts = Counter(key(item) for item in candidates)
    return {name: {"candidate_count": counts[name], "trade_count": 0,
                   "win_rate": None, "expectancy_r": None, "profit_factor": None}
            for name in dict.fromkeys((*required, *sorted(counts)))}


def _setup_breakdown(setups, triggered):
    detected = Counter(item[2] for item in setups)
    fired = Counter(item[2] for item in triggered)
    names = ("HIGH_OF_DAY_BREAKOUT", "MICRO_PULLBACK", "BULL_FLAG", "FLAT_TOP_BREAKOUT")
    return {name: {"detected": detected[name], "triggered": fired[name], "trade_count": 0,
                   "win_rate": None, "profit_factor": None, "expectancy_r": None,
                   "average_r": None, "median_r": None, "drawdown_r": None,
                   "mae_r": None, "mfe_r": None} for name in names}


def _write_empty_ledger(path: Path) -> None:
    fields = ("date_time", "symbol", "setup", "momentum_score", "entry", "stop",
              "risk_per_share", "position_size", "targets", "exit_prices", "realized_r",
              "mae", "mfe", "hold_duration", "catalyst_state", "rvol", "float_bucket",
              "price_bucket", "session", "reason_codes")
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(fields)


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value


def main() -> int:
    report = run(Path("data/warrior_momentum_v1_validation"),
                 Path("data/warrior_momentum_v1_validation/results"))
    print(json.dumps({"dataset": report["dataset"], "counts": report["counts"],
                      "conclusion": report["conclusion"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

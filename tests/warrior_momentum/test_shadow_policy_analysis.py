from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from inspect import signature
import json
from pathlib import Path
import sqlite3

from app.strategies.warrior_momentum.shadow_policy_analysis import (
    ShadowCaptureDataset,
    ShadowEvaluation,
    ShadowOutcome,
    ShadowPolicyAnalysisConfiguration,
    ShadowPolicyAnalyzer,
    group_analytical_opportunities,
    load_shadow_dataset_read_only,
)


T0 = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)


def evaluation(
    record_id: str,
    *,
    symbol: str = "XYZ",
    minute: int = 0,
    blockers: tuple[str, ...] = ("SPREAD_WIDE",),
    session: str = "REGULAR",
    scanner: str | None = "QUALIFYING",
    warrior: str = "QUALIFIED",
    setup_state: str | None = None,
    trigger: str | None = None,
    stop: str | None = None,
) -> ShadowEvaluation:
    valid = setup_state == "TRIGGERED" and trigger is not None and stop is not None
    return ShadowEvaluation(record_id, symbol, T0 + timedelta(minutes=minute), {
        "session": session,
        "reason_codes": blockers,
        "scanner_classification": scanner,
        "scanner_score": 90,
        "warrior_status": warrior,
        "warrior_score": "80",
        "setup_state": setup_state,
        "setup_type": "BULL_FLAG" if setup_state else None,
        "trigger": trigger,
        "stop": stop,
        "counterfactual_entry_valid": valid,
    })


def outcome(
    record_id: str,
    evaluation_id: str,
    *,
    horizon: int = 1,
    classification: str = "GOOD_REJECTION",
    status: str = "COMPLETE",
    return_percent: str | None = "-1",
    mfe: str | None = "0.2",
    mae: str | None = "-1",
    plan_state: str | None = None,
    reward: int | None = None,
    stopped: bool = False,
) -> ShadowOutcome:
    plan = None
    if plan_state is not None:
        plan = {
            "applicable": True,
            "state": plan_state,
            "stop_hit_at_bar": T0.isoformat() if stopped else None,
            "reward_hits": () if reward is None else (
                {"multiple": str(reward), "bar_timestamp": T0.isoformat()},
            ),
        }
    return ShadowOutcome(record_id, "XYZ", T0 + timedelta(minutes=horizon),
                         evaluation_id, {
        "evaluation_record_id": evaluation_id,
        "horizon_minutes": horizon,
        "status": status,
        "classification": classification if status == "COMPLETE" else "UNAVAILABLE",
        "return_percent": return_percent if status == "COMPLETE" else None,
        "mfe_percent": mfe if status == "COMPLETE" else None,
        "mae_percent": mae if status == "COMPLETE" else None,
        "hypothetical_trade": plan,
    })


def analyze(
    evaluations: tuple[ShadowEvaluation, ...],
    outcomes: tuple[ShadowOutcome, ...] = (),
):
    return ShadowPolicyAnalyzer().analyze(
        ShadowCaptureDataset(evaluations, outcomes, data_cutoff=T0),
        generated_at=T0,
    )


def test_raw_evaluations_are_distinct_from_gap_grouped_opportunities() -> None:
    values = (
        evaluation("e1", minute=0), evaluation("e2", minute=3),
        evaluation("e3", minute=9), evaluation("e4", symbol="ABC", minute=1),
    )
    groups = group_analytical_opportunities(values)
    report = analyze(values)

    assert len(groups) == 3
    assert report.evaluation_count == 4
    assert report.overall.unique_opportunity_count == 3
    assert groups[0].grouping_method.startswith("ANALYTICAL_GROUPING")


def test_single_and_two_blocker_cases_and_exact_combinations_are_separate() -> None:
    values = (
        evaluation("single", blockers=("SPREAD_WIDE",)),
        evaluation("two", symbol="ABC", blockers=("SPREAD_WIDE", "FLOAT_HIGH")),
    )
    report = analyze(values)

    assert report.blockers["SPREAD_WIDE"].evaluation_count == 2
    assert report.blocker_cohorts["SPREAD_WIDE"]["ONLY"].evaluation_count == 1
    assert report.exact_combinations["SPREAD_WIDE + FLOAT_HIGH"].evaluation_count == 1
    assert {len(item.blockers) for item in report.near_eligible} == {1, 2}


def test_triggered_plan_cohort_counts_plan_and_reward_once_across_horizons() -> None:
    value = evaluation(
        "triggered", setup_state="TRIGGERED", trigger="10.2", stop="10",
        blockers=("NO_CATALYST",),
    )
    outcomes = (
        outcome("o1", "triggered", classification="MISSED_OPPORTUNITY",
                plan_state="REACHED_REWARD", reward=1),
        outcome("o2", "triggered", horizon=2,
                classification="MISSED_OPPORTUNITY", plan_state="REACHED_REWARD", reward=1),
    )
    report = analyze((value,), outcomes)
    metrics = report.blockers["NO_CATALYST"]

    assert metrics.triggered_plan_count == 1
    assert metrics.one_r_reached_count == 1
    assert metrics.true_missed_opportunity_count == 2
    assert metrics.true_missed_opportunity_rate == D("1")
    assert report.triggered_plans[0].one_r_reached is True


def test_triggered_plan_without_complete_outcome_is_explicitly_unresolved() -> None:
    value = evaluation(
        "triggered", setup_state="TRIGGERED", trigger="10.2", stop="10",
    )
    report = analyze((value,), (
        outcome("o1", "triggered", status="INCOMPLETE_MISSING_FUTURE_DATA"),
    ))

    assert report.overall.triggered_plan_count == 1
    assert report.overall.triggered_unresolved_count == 1
    assert report.overall.true_missed_opportunity_rate == D("0")


def test_no_setup_deduplicates_repeated_observations_and_detects_later_setup() -> None:
    values = (
        evaluation("e1", minute=0, blockers=("NO_SETUP",)),
        evaluation("e2", minute=1, blockers=("NO_SETUP",)),
        evaluation("e3", minute=2, blockers=("NO_SETUP",), setup_state="FORMING"),
        evaluation("e4", symbol="ABC", minute=0, blockers=("NO_SETUP",)),
    )
    report = analyze(values)

    assert report.no_setup.evaluation_count == 4
    assert report.no_setup.unique_opportunity_count == 2
    assert report.no_setup.repeated_evaluation_count == 2
    assert report.no_setup.symbols_later_forming_or_triggered_count == 1
    assert report.no_setup.symbols_never_produced_setup_count == 1
    assert report.no_setup.symbol_later_forming_or_triggered_rate == D("0.5")
    assert report.no_setup.price_move_only_symbol_rate == D("0")
    assert report.no_setup.true_miss_symbol_rate == D("0")
    assert report.no_setup.later_forming_or_triggered_count == 1
    assert report.no_setup.never_produced_setup_count == 1
    assert report.no_setup.median_minutes_to_later_setup == D("2")


def test_independent_attribution_does_not_replace_exact_combination_attribution() -> None:
    values = (
        evaluation("e1", blockers=("NO_SETUP", "SPREAD_WIDE")),
        evaluation("e2", symbol="ABC", blockers=("NO_SETUP", "RISK_REJECTED")),
    )
    report = analyze(values)

    assert report.blockers["NO_SETUP"].evaluation_count == 2
    assert report.blockers["SPREAD_WIDE"].evaluation_count == 1
    assert report.blocker_cohorts["SPREAD_WIDE"]["WITH_NO_SETUP"].evaluation_count == 1
    assert report.blocker_cohorts["SPREAD_WIDE"]["WITHOUT_NO_SETUP"].evaluation_count == 0
    assert len(report.exact_combinations) == 2


def test_session_scanner_and_warrior_status_cohorts_are_available() -> None:
    values = (
        evaluation("e1", session="REGULAR", scanner="QUALIFYING", warrior="QUALIFIED"),
        evaluation("e2", symbol="ABC", session="PREMARKET", scanner="WATCHING",
                   warrior="WATCH"),
    )
    report = analyze(values)

    assert report.session_cohorts["REGULAR"].evaluation_count == 1
    assert report.session_cohorts["PREMARKET"].evaluation_count == 1
    assert report.scanner_cohorts["QUALIFYING"].evaluation_count == 1
    assert report.warrior_cohorts["WATCH"].evaluation_count == 1
    assert report.blocker_cohorts["SPREAD_WIDE"]["SCANNER_QUALIFYING"].evaluation_count == 1


def test_zero_denominators_and_incomplete_horizons_do_not_fabricate_rates() -> None:
    report = analyze((evaluation("e1"),), (
        outcome("o1", "e1", status="INCOMPLETE_MISSING_FUTURE_DATA"),
    ))
    horizon = report.overall.horizon_metrics[1]

    assert horizon.complete_count == 0
    assert horizon.positive_return_rate is None
    assert horizon.mean_return is None
    assert horizon.percentile_90_mfe is None
    assert report.overall.complete_horizon_count == 0


def test_true_miss_and_price_move_only_remain_distinct() -> None:
    values = (
        evaluation("price", blockers=("NO_SETUP",)),
        evaluation("true", symbol="ABC", blockers=("RISK_REJECTED",),
                   setup_state="TRIGGERED", trigger="10.2", stop="10"),
    )
    outcomes = (
        outcome("o1", "price", classification="MISSED_OPPORTUNITY_PRICE_MOVE_ONLY",
                return_percent="3", mfe="4"),
        outcome("o2", "true", classification="MISSED_OPPORTUNITY",
                return_percent="1", mfe="2", plan_state="REACHED_REWARD", reward=1),
    )
    report = analyze(values, outcomes)

    assert report.overall.price_move_only_miss_count == 1
    assert report.overall.true_missed_opportunity_count == 1
    assert report.triggered_plans[0].classification == "MISSED_OPPORTUNITY"


def test_strong_triggered_evidence_ranks_above_confounded_price_only_evidence() -> None:
    values = []
    outcomes = []
    for index, symbol in enumerate(("AAA", "BBB", "CCC")):
        record_id = f"true-{index}"
        values.append(evaluation(
            record_id, symbol=symbol, minute=index * 10,
            blockers=("RISK_REJECTED",), setup_state="TRIGGERED",
            trigger="10.2", stop="10",
            session="REGULAR" if index < 2 else "PREMARKET",
        ))
        outcomes.append(outcome(
            f"out-{index}", record_id, classification="MISSED_OPPORTUNITY",
            return_percent="2", mfe="3", plan_state="REACHED_REWARD", reward=2,
        ))
    for index in range(5):
        record_id = f"price-{index}"
        values.append(evaluation(
            record_id, symbol=f"P{index}", minute=50 + index * 10,
            blockers=("SPREAD_WIDE", "NO_SETUP", "FLOAT_HIGH"),
        ))
        outcomes.append(outcome(
            f"price-out-{index}", record_id,
            classification="MISSED_OPPORTUNITY_PRICE_MOVE_ONLY",
            return_percent="4", mfe="5",
        ))
    report = analyze(tuple(values), tuple(outcomes))
    ranking = {item.name: item for item in report.evidence_ranking}

    assert ranking["RISK_REJECTED"].score > ranking["SPREAD_WIDE"].score
    assert ranking["RISK_REJECTED"].recommendation == "STRONG_INVESTIGATION_CANDIDATE"
    assert ranking["SPREAD_WIDE"].recommendation != "INVESTIGATE"


def test_confounded_single_opportunity_receives_a_large_evidence_penalty() -> None:
    value = evaluation(
        "e1", blockers=("RISK_REJECTED", "NO_CATALYST", "FLOAT_HIGH"),
        setup_state="TRIGGERED", trigger="10.2", stop="10",
    )
    report = analyze((value,), (
        outcome("o1", "e1", classification="MISSED_OPPORTUNITY",
                plan_state="REACHED_REWARD", reward=1),
    ))
    ranking = {item.name: item for item in report.evidence_ranking}

    assert ranking["RISK_REJECTED"].score < 40
    assert ranking["RISK_REJECTED"].recommendation == "INSUFFICIENT_EVIDENCE"
    assert any("confounded" in reason for reason in ranking["RISK_REJECTED"].explanation)


def test_analysis_is_deterministic_for_the_same_cutoff() -> None:
    dataset = ShadowCaptureDataset((evaluation("e1"),), data_cutoff=T0)
    analyzer = ShadowPolicyAnalyzer()

    assert analyzer.analyze(dataset, generated_at=T0) == analyzer.analyze(
        dataset, generated_at=T0,
    )


def test_capture_loader_reads_shadow_records_without_mutating_store(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE capture_records ("
        "sequence INTEGER PRIMARY KEY, record_id TEXT, schema_version INTEGER, "
        "record_type TEXT, symbol TEXT, timestamp TEXT, payload_json TEXT)"
    )
    payload = {
        "session": "REGULAR", "reason_codes": ["NO_SETUP"],
        "counterfactual_entry_valid": False,
    }
    connection.execute(
        "INSERT INTO capture_records VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "eval", 1, "SHADOW_EVALUATION", "XYZ", T0.isoformat(),
         json.dumps(payload)),
    )
    connection.commit()
    connection.close()
    before = path.read_bytes()

    dataset = load_shadow_dataset_read_only(path)

    assert len(dataset.evaluations) == 1
    assert dataset.outcomes == ()
    assert path.read_bytes() == before


def test_analysis_has_no_trading_or_execution_surface() -> None:
    assert tuple(signature(ShadowPolicyAnalyzer).parameters) == ("config",)
    forbidden = {
        "submit_entry", "submit_exit", "place_order", "authorize_live",
        "trading_service", "order_gateway", "paper_order_gateway",
    }
    assert forbidden.isdisjoint(name.lower() for name in dir(ShadowPolicyAnalyzer))
    source = Path(
        "app/strategies/warrior_momentum/shadow_policy_analysis.py"
    ).read_text("utf-8")
    assert "app.services.trading_service" not in source
    assert "AutonomousPaperExecutionBridge" not in source
    assert "PaperOrderGateway" not in source

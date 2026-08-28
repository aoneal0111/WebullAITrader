from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from inspect import signature
import json
from pathlib import Path
import sqlite3

import pytest

from app.strategies.warrior_momentum.shadow_counterfactual_analysis import (
    CounterfactualOutcome,
    CounterfactualPolicy,
    CounterfactualStatus,
    ShadowCounterfactualAnalyzer,
)
from app.strategies.warrior_momentum.shadow_policy_analysis import (
    ShadowCaptureDataset,
    ShadowEvaluation,
    ShadowOutcome,
    load_shadow_dataset_read_only,
)


T0 = datetime(2026, 8, 27, 14, 0, tzinfo=UTC)


def evaluation(
    record_id: str,
    *,
    symbol: str = "XYZ",
    minute: int = 0,
    blockers: tuple[str, ...] = ("NO_CATALYST",),
    session: str = "REGULAR",
    setup_state: str | None = "TRIGGERED",
    setup_type: str | None = "BULL_FLAG",
    trigger: str | None = "10.20",
    stop: str | None = "10.00",
    plan_valid: bool | None = None,
    catalyst_state: str = "FALSE",
) -> ShadowEvaluation:
    if plan_valid is None:
        plan_valid = bool(
            setup_type is not None and setup_state == "TRIGGERED"
            and trigger is not None and stop is not None
            and D(trigger) > D(stop)
        )
    return ShadowEvaluation(record_id, symbol, T0 + timedelta(minutes=minute), {
        "session": session,
        "reason_codes": blockers,
        "setup_type": setup_type,
        "setup_state": setup_state,
        "trigger": trigger,
        "stop": stop,
        "counterfactual_entry_valid": plan_valid,
        "warrior_status": "INELIGIBLE_FOR_EXECUTION",
        "catalyst_state": catalyst_state,
        "horizons_minutes": (1,),
    })


def outcome(
    record_id: str,
    evaluation_id: str,
    *,
    status: str = "COMPLETE",
    horizon: int = 1,
    plan_state: str = "TRIGGERED_UNRESOLVED",
    triggered: bool = True,
    stop_minute: int | None = None,
    rewards: tuple[tuple[int, int], ...] = (),
    classification: str = "NEUTRAL_REJECTION",
    mfe: str = "1.5",
    mae: str = "-0.5",
) -> ShadowOutcome:
    payload: dict[str, object] = {
        "evaluation_record_id": evaluation_id,
        "horizon_minutes": horizon,
        "status": status,
        "classification": classification if status == "COMPLETE" else "UNAVAILABLE",
        "return_percent": "0.5" if status == "COMPLETE" else None,
        "mfe_percent": mfe if status == "COMPLETE" else None,
        "mae_percent": mae if status == "COMPLETE" else None,
    }
    if status == "COMPLETE":
        payload["hypothetical_trade"] = {
            "applicable": True,
            "state": plan_state,
            "triggered_at_bar": T0.isoformat() if triggered else None,
            "stop_hit_at_bar": (
                None if stop_minute is None
                else (T0 + timedelta(minutes=stop_minute)).isoformat()
            ),
            "reward_hits": tuple({
                "multiple": str(multiple),
                "bar_timestamp": (T0 + timedelta(minutes=minute)).isoformat(),
            } for multiple, minute in rewards),
            "same_bar_conflict_policy": "STOP_FIRST_CONSERVATIVE_1M_OHLC",
        }
    return ShadowOutcome(
        record_id, "XYZ", T0 + timedelta(minutes=horizon), evaluation_id, payload,
    )


def analyze(
    evaluations: tuple[ShadowEvaluation, ...],
    outcomes: tuple[ShadowOutcome, ...] = (),
):
    return ShadowCounterfactualAnalyzer().analyze(
        ShadowCaptureDataset(evaluations, outcomes, data_cutoff=T0),
        generated_at=T0,
    )


def result(report, policy: CounterfactualPolicy, evaluation_id: str = "e1"):
    return next(
        item for item in report.results
        if item.policy is policy and item.evaluation_id == evaluation_id
    )


def test_removes_exactly_one_selected_blocker_and_keeps_every_other_blocker() -> None:
    value = evaluation(
        "e1", blockers=("NO_CATALYST", "FLOAT_HIGH", "RISK_REJECTED"),
    )
    item = result(analyze((value,)), CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.authoritative_blockers == (
        "NO_CATALYST", "FLOAT_HIGH", "RISK_REJECTED",
    )
    assert item.counterfactual_remaining_blockers == (
        "FLOAT_HIGH", "RISK_REJECTED",
    )
    assert item.counterfactual_status is CounterfactualStatus.NOT_COUNTERFACTUAL_ENTRY_READY
    assert item.eligibility_changed is False


@pytest.mark.parametrize("policy", tuple(CounterfactualPolicy))
def test_rpd_four_blocker_regression_never_becomes_ready_when_one_is_removed(
    policy: CounterfactualPolicy,
) -> None:
    rpd = evaluation(
        "rpd", symbol="RPD",
        blockers=("RVOL_LOW", "FLOAT_HIGH", "NO_CATALYST", "RISK_REJECTED"),
        setup_type="HIGH_OF_DAY_BREAKOUT", trigger="13.646820", stop="13.61",
    )
    item = result(analyze((rpd,)), policy, "rpd")

    assert len(item.counterfactual_remaining_blockers) == 3
    assert policy.ignored_blocker not in item.counterfactual_remaining_blockers
    assert item.counterfactual_status is CounterfactualStatus.NOT_COUNTERFACTUAL_ENTRY_READY
    assert item.counterfactual_entry_ready is False
    assert item.execution_authorized is False


def test_catalyst_sole_blocker_triggered_plan_changes_only_analytical_eligibility() -> None:
    value = evaluation("e1", blockers=("NO_CATALYST",))
    item = result(analyze((value,)), CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.counterfactual_remaining_blockers == ()
    assert item.counterfactual_status is CounterfactualStatus.COUNTERFACTUAL_ENTRY_READY
    assert item.eligibility_changed is True
    assert item.counterfactual_entry_ready is True
    assert item.execution_authorized is False


def test_catalyst_plus_another_blocker_remains_blocked() -> None:
    value = evaluation("e1", blockers=("NO_CATALYST", "SPREAD_WIDE"))
    item = result(analyze((value,)), CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.counterfactual_remaining_blockers == ("SPREAD_WIDE",)
    assert item.counterfactual_entry_ready is False


@pytest.mark.parametrize(
    ("blockers", "setup_state", "setup_type", "trigger", "stop", "plan_valid"),
    (
        (("NO_CATALYST", "NO_SETUP"), None, None, None, None, False),
        (("NO_CATALYST",), "FORMING", "BULL_FLAG", "10.20", "10.00", False),
        (("NO_CATALYST",), "TRIGGERED", "BULL_FLAG", None, "10.00", False),
        (("NO_CATALYST",), "TRIGGERED", "BULL_FLAG", "10.20", None, False),
        (("NO_CATALYST",), "TRIGGERED", "BULL_FLAG", "10.00", "10.20", False),
    ),
)
def test_incomplete_or_unsafe_authoritative_plan_never_becomes_ready(
    blockers: tuple[str, ...], setup_state: str | None, setup_type: str | None,
    trigger: str | None, stop: str | None, plan_valid: bool,
) -> None:
    value = evaluation(
        "e1", blockers=blockers, setup_state=setup_state, setup_type=setup_type,
        trigger=trigger, stop=stop, plan_valid=plan_valid,
    )
    item = result(analyze((value,)), CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.counterfactual_status is CounterfactualStatus.NOT_COUNTERFACTUAL_ENTRY_READY
    assert item.execution_authorized is False


def test_captured_plan_validity_flag_remains_authoritative() -> None:
    value = evaluation("e1", plan_valid=False)
    item = result(analyze((value,)), CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.authoritative_plan_valid is False
    assert item.counterfactual_entry_ready is False


def test_ignore_risk_is_explicitly_unapproved_and_never_executable() -> None:
    value = evaluation("e1", blockers=("RISK_REJECTED",), catalyst_state="TRUE")
    item = result(analyze((value,)), CounterfactualPolicy.IGNORE_RISK_REJECTED)

    assert item.counterfactual_status is (
        CounterfactualStatus.COUNTERFACTUAL_POLICY_ELIGIBLE_RISK_UNAPPROVED
    )
    assert item.eligibility_changed is True
    assert item.counterfactual_entry_ready is False
    assert item.execution_authorized is False


def test_trigger_crossed_and_unresolved_are_reported_from_complete_plan() -> None:
    report = analyze((evaluation("e1"),), (outcome("o1", "e1"),))
    item = result(report, CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.trigger_crossed is True
    assert item.sufficient_outcome_evidence is True
    assert item.counterfactual_outcome is CounterfactualOutcome.COUNTERFACTUAL_UNRESOLVED


def test_never_triggered_is_distinct_from_unresolved() -> None:
    report = analyze((evaluation("e1"),), (
        outcome("o1", "e1", plan_state="NEVER_TRIGGERED", triggered=False),
    ))
    item = result(report, CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.trigger_crossed is False
    assert item.counterfactual_outcome is (
        CounterfactualOutcome.COUNTERFACTUAL_NEVER_TRIGGERED
    )


def test_stop_is_a_resolved_counterfactual_outcome() -> None:
    report = analyze((evaluation("e1"),), (
        outcome("o1", "e1", plan_state="HIT_STOP", stop_minute=1),
    ))
    item = result(report, CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.stop_hit is True
    assert item.counterfactual_outcome is CounterfactualOutcome.COUNTERFACTUAL_STOP


@pytest.mark.parametrize(
    ("rewards", "expected"),
    (
        (((1, 1),), CounterfactualOutcome.COUNTERFACTUAL_WIN_1R),
        (((1, 1), (2, 2)), CounterfactualOutcome.COUNTERFACTUAL_WIN_2R),
    ),
)
def test_reward_outcomes_distinguish_one_and_two_r(
    rewards: tuple[tuple[int, int], ...], expected: CounterfactualOutcome,
) -> None:
    report = analyze((evaluation("e1"),), (
        outcome("o1", "e1", plan_state="REACHED_REWARD", rewards=rewards),
    ))
    item = result(report, CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.one_r_reached is True
    assert item.two_r_reached is (expected is CounterfactualOutcome.COUNTERFACTUAL_WIN_2R)
    assert item.counterfactual_outcome is expected


def test_same_bar_reward_and_stop_is_conservatively_stop_first() -> None:
    report = analyze((evaluation("e1"),), (
        outcome(
            "o1", "e1", plan_state="HIT_STOP", stop_minute=1,
            rewards=((1, 1),), mfe="3", mae="-3",
        ),
    ))
    item = result(report, CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.one_r_reached is False
    assert item.stop_hit is True
    assert item.counterfactual_outcome is CounterfactualOutcome.COUNTERFACTUAL_STOP


def test_reward_before_later_stop_remains_a_win() -> None:
    report = analyze((evaluation("e1"),), (
        outcome(
            "o1", "e1", plan_state="HIT_STOP", stop_minute=2,
            rewards=((1, 1),),
        ),
    ))
    item = result(report, CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.one_r_reached is True
    assert item.counterfactual_outcome is CounterfactualOutcome.COUNTERFACTUAL_WIN_1R


def test_incomplete_data_never_fabricates_a_trade_outcome() -> None:
    report = analyze((evaluation("e1"),), (
        outcome("o1", "e1", status="INCOMPLETE_MISSING_FUTURE_DATA"),
    ))
    item = result(report, CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.outcome_available is False
    assert item.sufficient_outcome_evidence is False
    assert item.counterfactual_outcome is (
        CounterfactualOutcome.COUNTERFACTUAL_INCOMPLETE_DATA
    )


def test_repeated_evaluations_are_raw_results_but_one_analytical_opportunity() -> None:
    values = (
        evaluation("e1", minute=0), evaluation("e2", minute=3),
        evaluation("e3", symbol="ABC", minute=10),
    )
    report = analyze(values)
    catalyst = report.policies[CounterfactualPolicy.IGNORE_NO_CATALYST]

    assert report.evaluation_count == 3
    assert report.unique_opportunity_count == 2
    assert catalyst.raw_evaluations_containing_blocker == 3
    assert catalyst.unique_opportunities_containing_blocker == 2
    assert catalyst.blocker_symbol_count == 2
    assert catalyst.blocker_session_count == 1
    assert catalyst.eligibility_changes == 3
    assert catalyst.eligibility_change_opportunities == 2


def test_cross_policy_comparison_and_evidence_ranking_reward_isolated_proof() -> None:
    values = []
    outcomes = []
    for index, (symbol, session) in enumerate((
        ("AAA", "REGULAR"), ("BBB", "PREMARKET"), ("CCC", "AFTER_HOURS"),
    )):
        record_id = f"cat-{index}"
        values.append(evaluation(
            record_id, symbol=symbol, minute=index * 10,
            blockers=("NO_CATALYST",), session=session,
        ))
        outcomes.append(outcome(
            f"out-{index}", record_id, plan_state="REACHED_REWARD",
            rewards=((1, 1),), classification="MISSED_OPPORTUNITY",
        ))
    values.append(evaluation(
        "rvol", symbol="DDD", minute=40,
        blockers=("RVOL_LOW", "FLOAT_HIGH"), catalyst_state="TRUE",
    ))
    report = analyze(tuple(values), tuple(outcomes))
    catalyst = report.policies[CounterfactualPolicy.IGNORE_NO_CATALYST]
    rvol = report.policies[CounterfactualPolicy.IGNORE_RVOL_LOW]

    assert catalyst.evidence_score > rvol.evidence_score
    assert catalyst.recommendation == "STRONG_RESEARCH_CANDIDATE"
    assert rvol.eligibility_changes == 0
    assert rvol.recommendation == "INSUFFICIENT_EVIDENCE"


@pytest.mark.parametrize(
    ("state", "cohort"),
    (
        ("TRUE", "CONFIRMED_CATALYST_PRESENT"),
        ("FALSE", "CONFIRMED_NO_CATALYST"),
        ("UNKNOWN", "CATALYST_UNKNOWN_OR_UNVERIFIED"),
        ("UNAVAILABLE", "CATALYST_EVIDENCE_UNAVAILABLE_CAUSE_UNSPECIFIED"),
    ),
)
def test_catalyst_evidence_states_are_not_conflated(state: str, cohort: str) -> None:
    value = evaluation("e1", catalyst_state=state)
    item = result(analyze((value,)), CounterfactualPolicy.IGNORE_NO_CATALYST)

    assert item.catalyst_evidence_cohort == cohort


def test_read_only_capture_analysis_does_not_mutate_database(tmp_path: Path) -> None:
    path = tmp_path / "capture.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE capture_records ("
        "sequence INTEGER PRIMARY KEY, record_id TEXT, schema_version INTEGER, "
        "record_type TEXT, symbol TEXT, timestamp TEXT, payload_json TEXT)"
    )
    captured = evaluation("e1")
    connection.execute(
        "INSERT INTO capture_records VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, captured.record_id, 1, "SHADOW_EVALUATION", captured.symbol,
         captured.timestamp.isoformat(), json.dumps(captured.payload)),
    )
    connection.commit()
    connection.close()
    before = path.read_bytes()

    dataset = load_shadow_dataset_read_only(path)
    ShadowCounterfactualAnalyzer().analyze(dataset, generated_at=T0)

    assert path.read_bytes() == before


def test_counterfactual_analyzer_has_no_trading_or_execution_surface() -> None:
    assert tuple(signature(ShadowCounterfactualAnalyzer).parameters) == ("config",)
    forbidden = {
        "submit_entry", "submit_exit", "place_order", "authorize_live",
        "trading_service", "order_gateway", "paper_order_gateway",
    }
    assert forbidden.isdisjoint(
        name.lower() for name in dir(ShadowCounterfactualAnalyzer)
    )
    source = Path(
        "app/strategies/warrior_momentum/shadow_counterfactual_analysis.py"
    ).read_text("utf-8")
    for forbidden_import in (
        "TradingService", "AutonomousPaperExecutionBridge", "PaperOrderGateway",
        "WarriorMomentumRuntime", "app.services", ".runtime", ".forward_runtime",
    ):
        assert forbidden_import not in source

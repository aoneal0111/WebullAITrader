from __future__ import annotations

from dataclasses import replace
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

import pytest

from app.trade_intelligence.analogs import AnalogQuery, HistoricalAnalogEngine
from app.trade_intelligence.experience_store import ExperienceStore
from app.trade_intelligence.models import (
    AtlasDecision, MissedOpportunityClassification, OutcomeStatus, PriceBar,
    canonical_json,
)
from app.trade_intelligence.outcome_engine import OutcomeEngine, classify_missed_opportunity
from app.trade_intelligence.reporting import ExperienceReporter
from tests.trade_intelligence.conftest import T0, make_experience


def path(values, symbol="ABCD"):
    return tuple(
        PriceBar(symbol, T0 + timedelta(minutes=index), Decimal("10"), Decimal(high), Decimal(low), Decimal(close), Decimal("1000"))
        for index, (high, low, close) in enumerate(values)
    )


def test_exact_horizons_mfe_mae_and_1r_2r_3r():
    bars = path([(Decimal("10.6") + Decimal(index) / 10, Decimal("9.8"), Decimal("10.2")) for index in range(30)])
    outcomes = OutcomeEngine().evaluate(make_experience(), bars)
    assert [item.horizon_minutes for item in outcomes] == [1, 2, 5, 10, 15, 30]
    one = outcomes[0]
    assert one.future_price == Decimal("10.2")
    assert one.mfe == Decimal("0.6") and one.mae == Decimal("-0.2")
    assert one.reached_1r and not one.reached_2r
    assert outcomes[-1].reached_3r


def test_same_bar_stop_first_is_conservative():
    outcome = OutcomeEngine().evaluate(
        make_experience(), path([(Decimal("11.5"), Decimal("9.4"), Decimal("10"))])
    )[0]
    assert outcome.first_plan_event == "STOP"
    assert outcome.stop_reached and not outcome.reached_1r
    assert classify_missed_opportunity(make_experience(), outcome) is MissedOpportunityClassification.PROTECTED_REJECTION


def test_profitable_missed_and_dangerous_false_positive_definitions():
    profitable = OutcomeEngine().evaluate(
        make_experience(), path([(Decimal("11.1"), Decimal("9.8"), Decimal("11"))])
    )[0]
    assert classify_missed_opportunity(make_experience(), profitable) is MissedOpportunityClassification.PROFITABLE_MISSED_OPPORTUNITY
    dangerous = OutcomeEngine().evaluate(
        make_experience(), path([
            (Decimal("10.6"), Decimal("9.8"), Decimal("10.5")),
            (Decimal("10.7"), Decimal("9.4"), Decimal("9.6")),
        ])
    )[1]
    assert classify_missed_opportunity(make_experience(), dangerous) is MissedOpportunityClassification.DANGEROUS_FALSE_POSITIVE


def test_missing_gaps_out_of_order_and_duplicate_bars_are_deterministic():
    bars = path([
        (Decimal("10.2"), Decimal("9.8"), Decimal("10")),
        (Decimal("10.3"), Decimal("9.9"), Decimal("10.1")),
        (Decimal("10.4"), Decimal("10"), Decimal("10.2")),
    ])
    reordered = (bars[2], bars[0], bars[1], bars[1])
    outcomes = OutcomeEngine().evaluate(make_experience(), reordered, finalize_missing=True)
    assert outcomes[0].status is OutcomeStatus.COMPLETE
    assert outcomes[-1].status is OutcomeStatus.INSUFFICIENT_DATA
    gap = OutcomeEngine().evaluate(make_experience(), (bars[0], bars[2]), finalize_missing=True)
    assert any(item.unavailable_reason == "NONCONTIGUOUS_MINUTE_BARS" for item in gap)


def test_gap_remains_provisional_during_bounded_reorder_window():
    bars = path([
        (Decimal("10.2"), Decimal("9.8"), Decimal("10")),
        (Decimal("10.3"), Decimal("9.9"), Decimal("10.1")),
        (Decimal("10.4"), Decimal("9.9"), Decimal("10.2")),
        (Decimal("10.5"), Decimal("9.9"), Decimal("10.3")),
    ])
    provisional = OutcomeEngine().evaluate(make_experience(), (bars[0], bars[2]))
    assert {item.horizon_minutes for item in provisional} == {1}

    completed_after_late_bar = OutcomeEngine().evaluate(
        make_experience(), (bars[2], bars[0], bars[1], bars[1]),
    )
    assert {item.horizon_minutes for item in completed_after_late_bar} >= {1, 2}
    assert all(item.status is OutcomeStatus.COMPLETE for item in completed_after_late_bar)

    terminal = OutcomeEngine().evaluate(make_experience(), (bars[0], bars[2], bars[3]))
    two_minute = next(item for item in terminal if item.horizon_minutes == 2)
    assert two_minute.status is OutcomeStatus.INSUFFICIENT_DATA
    assert two_minute.unavailable_reason == "NONCONTIGUOUS_MINUTE_BARS"


def test_repeated_outcome_evaluation_has_identical_immutable_content():
    bars = path([
        (Decimal("10.6"), Decimal("9.8"), Decimal("10.2")),
        (Decimal("11.1"), Decimal("9.7"), Decimal("10.8")),
    ])
    first = OutcomeEngine().evaluate(make_experience(), bars)
    repeated = OutcomeEngine().evaluate(make_experience(), (bars[1], bars[0], bars[0]))
    assert tuple(canonical_json(asdict(item)) for item in first) == tuple(
        canonical_json(asdict(item)) for item in repeated
    )


def test_conflicting_duplicate_completed_bar_is_rejected():
    bars = path([(Decimal("10.2"), Decimal("9.8"), Decimal("10"))])
    conflicting = replace(bars[0], close=Decimal("10.1"))
    with pytest.raises(ValueError, match="completed bar identity"):
        OutcomeEngine().evaluate(make_experience(), (bars[0], conflicting))


def test_planless_experience_never_fabricates_hypothetical_execution():
    base = make_experience()
    snapshot = replace(base.snapshot, trigger_price=None, structural_stop=None, risk_per_share=None)
    exp = replace(base, snapshot=snapshot, technically_actionable=False)
    outcome = OutcomeEngine().evaluate(exp, path([(Decimal("12"), Decimal("9"), Decimal("11"))]))[0]
    assert outcome.reached_1r is None and outcome.stop_reached is None
    assert classify_missed_opportunity(exp, outcome) is MissedOpportunityClassification.INSUFFICIENT_OUTCOME_DATA


def test_analogs_use_only_prior_decision_features_and_minimum_evidence(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    prior = make_experience(at=T0 - timedelta(days=1))
    target = make_experience(episode="target")
    future = make_experience(episode="future", at=T0 + timedelta(days=1))
    for item in (prior, target, future):
        store.put_experience(item)
    for outcome in OutcomeEngine().evaluate(prior, tuple(replace(item, timestamp=item.timestamp - timedelta(days=1)) for item in path([(Decimal("11.1"), Decimal("9.8"), Decimal("11"))]))):
        store.put_outcome(outcome)
    result = HistoricalAnalogEngine(store).query(AnalogQuery(target, T0, minimum_sample_size=1))
    assert result.experience_ids == (prior.experience_id,)
    assert result.evidence_sufficient and result.reached_2r_rate == Decimal("1")


def test_reporting_answers_rejected_blocker_cohorts(tmp_path):
    store = ExperienceStore(tmp_path / "memory.sqlite3")
    exp = make_experience()
    store.put_experience(exp)
    for outcome in OutcomeEngine().evaluate(exp, path([(Decimal("11.1"), Decimal("9.8"), Decimal("11"))])):
        store.put_outcome(outcome)
    report = ExperienceReporter(store).summary()
    assert report["unique_experiences"] == 1
    assert report["rejected"] == 1
    assert report["by_blocker"] == {"NO_CATALYST": 1}
    assert report["classifications"]["PROFITABLE_MISSED_OPPORTUNITY"] == 1

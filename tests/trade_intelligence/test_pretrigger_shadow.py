from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, ScannerObservation,
)
from app.strategies.warrior_momentum.forward_models import (
    CaptureRecordType, PointInTimeObservation,
)
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.models import MinuteBar
from app.trade_intelligence.decision_intelligence.models import HistoricalIntelligenceResult
from app.trade_intelligence.pretrigger_shadow import (
    AdaptivePretriggerShadowIntelligence, SHADOW_VERSION,
)


NOW = datetime(2026, 9, 15, 12, 30, tzinfo=UTC)
PROVENANCE = "taxonomy-structural-provenance-v1|XYZ|2026-09-15|PREMARKET|BREAKOUT_ROOT|2026-09-15T12:20:00+00:00"


class _Writer:
    def __init__(self):
        self.records = []

    def submit(self, record):
        self.records.append(record)


def _bars():
    return (
        MinuteBar("XYZ", NOW - timedelta(minutes=2), D("9.80"), D("9.90"), D("9.75"), D("9.82"), D("100")),
        MinuteBar("XYZ", NOW - timedelta(minutes=1), D("9.82"), D("9.98"), D("9.80"), D("9.95"), D("200")),
    )


def _value(*, price="9.95", volume="100000", spread="1.25",
           catalyst_status=CatalystStatus.UNAVAILABLE, catalyst_source=None):
    price_value = D(price)
    half = price_value * D(spread) / D("200")
    observation = ScannerObservation(
        symbol="XYZ", timestamp=NOW, price=price_value,
        previous_close=D("8"), current_volume=D(volume),
        average_30_day_volume=D("25000"), float_shares=D("5000000"),
        bid=price_value - half, ask=price_value + half,
        catalyst=CatalystType.EARNINGS if catalyst_status is CatalystStatus.TRUE else CatalystType.NONE,
        catalyst_headline="verified" if catalyst_status is CatalystStatus.TRUE else None,
        tradable=True, halted=False, asset_class=AssetClass.STOCK,
        catalyst_status=catalyst_status, catalyst_source=catalyst_source,
        catalyst_published_at=(NOW - timedelta(hours=1) if catalyst_status is CatalystStatus.TRUE else None),
    )
    return PointInTimeObservation(
        observation=observation, session="PREMARKET", bars=_bars(),
        quote_observed_at=NOW, quote_freshness_seconds=D("0.2"),
        last_price_observed_at=NOW, last_price_freshness_seconds=D("0.2"),
        evaluation_timestamp=NOW, quote_provenance="SHARED_SCANNER_ADAPTER",
    )


def _candidate(*, dollar_volume="995000", momentum="75", setup="75"):
    return SimpleNamespace(
        symbol="XYZ", relative_volume=D("4"), dollar_volume=D(dollar_volume),
        score=SimpleNamespace(total=D(momentum)),
        setup=SimpleNamespace(score=D(setup)),
    )


def _result(*, opportunity="opportunity-1", strategy="HIGH_OF_DAY_BREAKOUT",
            trigger="10.00", stop="9.85", location="APPROACHING_TRIGGER",
            stage="TRIGGER_READY"):
    readiness = () if stage != "TRIGGER_READY" else ({
        "strategy": strategy, "state": "TRIGGER_ARMED", "trigger": D(trigger),
        "structural_stop": D(stop), "trigger_source": "TAXONOMY_COMPLETED_BAR",
        "stop_source": "TAXONOMY_STRUCTURAL_STOP",
        "opportunity_anchor": opportunity, "detector_episode_id": "detector-1",
        "structural_provenance": PROVENANCE,
    },)
    return HistoricalIntelligenceResult(
        opportunity_id=opportunity, trading_date="2026-09-15", session="PREMARKET",
        recognized_memberships=(strategy, "POST_GAP_RECLAIM"),
        primary_strategy=strategy, setup_state="TRIGGER_ARMED", setup_stage=stage,
        entry_location=location, trigger_price=D(trigger), structural_stop=D(stop),
        current_price=D("9.95"), relative_volume=D("4"),
        readiness_memberships=readiness, structural_anchor=opportunity,
        confidence="STRONG_EVIDENCE", evaluated_at=NOW,
        setup_evidence=({"strategy": strategy, "confidence_state": "STRONG_EVIDENCE",
                         "test_sample_count": 0, "walk_forward_state": "UNAVAILABLE",
                         "median_mfe": "3.0"},),
    )


def _evaluate(*, value=None, candidate=None, result=None, conflicts=()):
    observer = AdaptivePretriggerShadowIntelligence(_Writer())
    return observer.evaluate(
        value=value or _value(), candidate=candidate or _candidate(),
        result=result or _result(), lifecycle_conflicts=conflicts,
    )


def test_trigger_ready_taxonomy_candidate_surfaces_without_conventional_signal_and_cannot_authorize():
    writer = _Writer()
    observer = AdaptivePretriggerShadowIntelligence(writer)
    evaluation = observer.observe(
        value=_value(), candidate=_candidate(), result=_result(),
        conventional_signal=None,
    )
    assert evaluation is not None
    assert evaluation.candidate.opportunity_id == "opportunity-1"
    assert evaluation.candidate.trigger == D("10")
    assert evaluation.candidate.structural_stop == D("9.85")
    assert evaluation.candidate.structural_provenance == PROVENANCE
    assert evaluation.execution_authority is False
    assert evaluation.real_risk_reserved is False
    assert evaluation.real_order_created is False
    assert evaluation.broker_placement_possible is False
    assert {item.record_type for item in writer.records} >= {
        CaptureRecordType.SHADOW_EVALUATION,
        CaptureRecordType.SHADOW_LATCHED_PLAN,
        CaptureRecordType.SHADOW_OUTCOME,
    }


def test_forward_runtime_observes_shadow_without_creating_a_real_paper_record(tmp_path):
    store = ForwardCaptureStore(tmp_path / "forward-runtime.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        decision_intelligence_observer=lambda **_kwargs: _result(),
    )
    try:
        _candidate_result, control_signal = service.observe(_value())
        writer.flush()
        assert control_signal is None
        assert store.records(record_type=CaptureRecordType.SHADOW_EVALUATION)
        assert not store.records(record_type=CaptureRecordType.PAPER_FILL)
    finally:
        writer.close()


@pytest.mark.parametrize("strategy", ["HIGH_OF_DAY_BREAKOUT", "FLAT_TOP_BREAKOUT"])
def test_standard_primary_strategies_are_eligible(strategy):
    evaluation = _evaluate(result=_result(strategy=strategy))
    assert evaluation.classification == "STANDARD_ELIGIBLE"
    assert evaluation.standard_would_authorize_probe is True
    assert evaluation.hypothetical_probe_risk_fraction == D("0.25")


@pytest.mark.parametrize(
    ("dollar_volume", "eligible"),
    [("499999", False), ("500000", True)],
)
def test_standard_current_day_dollar_volume_boundary(dollar_volume, eligible):
    evaluation = _evaluate(candidate=_candidate(dollar_volume=dollar_volume))
    assert evaluation.standard_would_authorize_probe is eligible
    assert evaluation.dollar_volume_bucket == (
        "$250,000-<$500,000" if not eligible else "$500,000-<$1,000,000"
    )


@pytest.mark.parametrize(
    ("spread", "eligible", "bucket"),
    [
        ("1.25", True, ">1.00% <=1.25%"),
        ("1.26", True, ">1.25% <=1.60%"),
        ("1.60", True, ">1.25% <=1.60%"),
        ("1.61", False, ">1.60% <=2.00%"),
    ],
)
def test_standard_spread_boundaries(spread, eligible, bucket):
    evaluation = _evaluate(value=_value(spread=spread))
    assert evaluation.standard_would_authorize_probe is eligible
    assert evaluation.spread_bucket == bucket


def test_standard_rvol_distance_and_extension_boundaries():
    at_boundaries = _evaluate(
        value=_value(price="9.95"), result=_result(trigger="10", stop="9.85"),
        candidate=_candidate(),
    )
    assert at_boundaries.pretrigger_distance_r == D("0.5")
    assert at_boundaries.pretrigger_distance_percent < D("1")
    assert at_boundaries.standard_would_authorize_probe
    low_rvol = _candidate()
    low_rvol.relative_volume = D("1.999")
    assert not _evaluate(candidate=low_rvol).standard_would_authorize_probe
    exact_rvol = _candidate()
    exact_rvol.relative_volume = D("2.0")
    assert _evaluate(candidate=exact_rvol).standard_would_authorize_probe
    assert not _evaluate(result=_result(location="SLIGHTLY_EXTENDED")).standard_would_authorize_probe


def test_one_percent_and_half_r_distance_boundaries_both_pass():
    evaluation = _evaluate(
        value=_value(price="10.00"),
        result=_result(trigger="10.10", stop="9.80"),
    )
    assert evaluation.pretrigger_distance_r == D("0.5")
    assert evaluation.pretrigger_distance_percent == D("1.00")
    assert evaluation.standard_would_authorize_probe


@pytest.mark.parametrize(
    ("dollar_volume", "spread", "classification"),
    [
        ("350000", "1.25", "EXCEPTIONAL_OVERRIDE_CANDIDATE"),
        ("200000", "1.25", "VERY_LOW_LIQUIDITY_EXCEPTION"),
        ("700000", "1.80", "EXCEPTIONAL_OVERRIDE_CANDIDATE"),
        ("700000", "2.10", "EXTREME_SPREAD_EXCEPTION"),
    ],
)
def test_exceptional_research_bands_are_separate_and_non_authorizing(
    dollar_volume, spread, classification,
):
    evaluation = _evaluate(
        value=_value(spread=spread),
        candidate=_candidate(dollar_volume=dollar_volume),
    )
    assert evaluation.classification == classification
    assert evaluation.exceptional_override_candidate
    assert evaluation.execution_authority is False
    assert evaluation.broker_placement_possible is False


def test_catalyst_is_captured_only_from_real_evidence_and_non_catalyst_strength_is_supported():
    unavailable = _evaluate(value=_value(catalyst_status=CatalystStatus.UNAVAILABLE),
                            candidate=_candidate(dollar_volume="350000"))
    assert unavailable.candidate.catalyst_fresh is False
    assert unavailable.catalyst_override_candidate is False
    assert "CATALYST_UNAVAILABLE" in unavailable.reasons
    assert unavailable.exceptional_override_candidate is True

    verified = _evaluate(
        value=_value(catalyst_status=CatalystStatus.TRUE, catalyst_source="WEBULL_NEWS"),
        candidate=_candidate(dollar_volume="350000"),
    )
    assert verified.candidate.catalyst_fresh is True
    assert verified.catalyst_override_candidate is True
    assert "CATALYST_VERIFIED" in verified.reasons


def test_point_in_time_liquidity_rvol_and_spread_evolution_are_recorded():
    observer = AdaptivePretriggerShadowIntelligence(_Writer())
    observer.evaluate(value=_value(spread="1.50"), candidate=_candidate(dollar_volume="600000"),
                      result=_result())
    later = observer.evaluate(
        value=_value(spread="1.25"),
        candidate=_candidate(dollar_volume="900000"), result=_result(),
    )
    assert later.candidate.current_day_dollar_volume_acceleration == D("1.5")
    assert later.candidate.relative_volume_acceleration == D("1")
    assert later.candidate.spread_trend == "IMPROVING"


def test_failures_and_historical_validation_never_grant_authority():
    for evaluation in (
        _evaluate(result=_result(strategy="FIRST_PULLBACK")),
        _evaluate(result=_result(stage="FORMING")),
        _evaluate(conflicts=("ACTIVE_POSITION",)),
    ):
        assert not evaluation.standard_would_authorize_probe
        assert evaluation.execution_authority is False
        assert evaluation.candidate.historical_validation_passed is False


def test_one_shadow_probe_per_opportunity_survives_confluence_and_restart(tmp_path):
    store = ForwardCaptureStore(tmp_path / "forward.sqlite3")
    fingerprint = "test-configuration"
    first_writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    first = AdaptivePretriggerShadowIntelligence(
        first_writer, configuration_fingerprint=fingerprint, store=store,
    )
    first.observe(value=_value(), candidate=_candidate(), result=_result())
    first.observe(value=replace(_value(), evaluation_timestamp=NOW + timedelta(seconds=5)),
                  candidate=_candidate(), result=replace(
                      _result(), recognized_memberships=("HIGH_OF_DAY_BREAKOUT", "FLAT_TOP_BREAKOUT")))
    first_writer.close()

    second_writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    restarted = AdaptivePretriggerShadowIntelligence(
        second_writer, configuration_fingerprint=fingerprint, store=store,
    )
    assert restarted.memory_metrics()["active_shadow_contexts"] == 1
    restarted.observe(value=replace(_value(), evaluation_timestamp=NOW + timedelta(minutes=1)),
                      candidate=_candidate(), result=_result())
    restarted.observe(value=replace(_value(), evaluation_timestamp=NOW + timedelta(minutes=1)),
                      candidate=_candidate(), result=_result(opportunity="opportunity-2"))
    second_writer.close()

    plans = store.records(record_type=CaptureRecordType.SHADOW_LATCHED_PLAN)
    assert len(plans) == 2
    assert {item.payload["opportunity_id"] for item in plans} == {
        "opportunity-1", "opportunity-2",
    }


def test_confirmation_add_is_shadow_only_and_state_is_bounded():
    writer = _Writer()
    observer = AdaptivePretriggerShadowIntelligence(writer, state_limit=2)
    for index in range(3):
        observer.observe(
            value=replace(_value(), evaluation_timestamp=NOW + timedelta(minutes=index)),
            candidate=_candidate(), result=_result(opportunity=f"opportunity-{index}"),
        )
    confirmed = replace(
        _result(opportunity="opportunity-2"), setup_state="TRIGGERED",
        setup_stage="TRIGGERED", readiness_memberships=(),
    )
    observer.observe(
        value=replace(_value(), evaluation_timestamp=NOW + timedelta(minutes=4)),
        candidate=_candidate(), result=confirmed, conventional_signal=object(),
    )
    policy = [item.payload for item in writer.records
              if item.record_type is CaptureRecordType.SHADOW_POLICY_RESULT]
    assert policy[-1]["confirmation_add"] == "SHADOW_ONLY"
    assert policy[-1]["remaining_hypothetical_risk_fraction"] == "0.75"
    assert policy[-1]["execution_authority"] is False
    metrics = observer.memory_metrics()
    assert metrics["active_shadow_contexts"] <= 2
    assert metrics["evaluation_dedupe_contexts"] <= 2
    assert metrics["market_evolution_contexts"] <= 2
    assert metrics["maximum_contexts"] == 2


def test_shadow_version_is_explicit():
    assert SHADOW_VERSION == "ADAPTIVE_PRETRIGGER_ENTRY_V1_SHADOW"

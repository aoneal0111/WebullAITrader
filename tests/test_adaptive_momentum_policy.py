from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.momentum_scanner import (
    AssetClass, CatalystStatus, CatalystType, ExecutionQuality,
    FloatProvenance, ScannerObservation, evaluate_candidate,
)
from app.momentum_scanner.quality import rvol_quality, velocity_attention
from app.scanner_adapter.evaluation_mailbox import LatestEvaluationMailbox
from app.scanner_adapter.pipeline import MomentumScannerPipeline
from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
from app.strategies.warrior_momentum.models import (
    CandidateStatus, MomentumCandidate, MomentumScore, ReasonCode,
    SetupDetection, SetupState, SetupType, StopModel,
)
from app.strategies.warrior_momentum.runtime import WarriorMomentumRuntime, entry_rejections

D = Decimal
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)


def observation(
    *, symbol="RUN", change="100", rvol="1.5", dollar="21000000",
    spread="0.8", price="5",
):
    price_value = D(price)
    volume = D(dollar) / price_value
    half = price_value * D(spread) / D("200")
    return ScannerObservation(
        symbol=symbol, timestamp=NOW, price=price_value,
        previous_close=price_value / (D("1") + D(change) / D("100")),
        current_volume=volume, average_30_day_volume=volume / D(rvol),
        float_shares=D("8000000"), bid=price_value - half,
        ask=price_value + half, catalyst=CatalystType.CONTRACT,
        catalyst_headline="material contract", tradable=True, halted=False,
        asset_class=AssetClass.STOCK, catalyst_status=CatalystStatus.TRUE,
        float_provenance=FloatProvenance.AUTHORITATIVE_FLOAT,
    )


def candidate(*, spread="4", rvol="0.8", risk="0.20"):
    trigger = D("5")
    stop = trigger - D(risk)
    return MomentumCandidate(
        rank=1, symbol="RUN", timestamp=NOW, price=trigger,
        percentage_change=D("80"), relative_volume=D(rvol),
        float_shares=D("5000000"), volume=D("4000000"),
        dollar_volume=D("20000000"), spread_percent=D(spread),
        catalyst_status=CatalystStatus.TRUE,
        catalyst_type=CatalystType.CONTRACT,
        score=MomentumScore(D("90"), ()), stocks_in_play=(),
        setup=SetupDetection(
            SetupType.FLAT_TOP_BREAKOUT, SetupState.TRIGGERED, D("90"),
            trigger, stop, StopModel.BREAKOUT_LEVEL,
        ),
        session="REGULAR", status=CandidateStatus.QUALIFIED,
        tradable=True, halted=False, distance_from_hod_percent=D("0.2"),
        reason_codes=(ReasonCode.RVOL_LOW, ReasonCode.SPREAD_WIDE),
        explanations=(), discovery_qualified=True,
        bid=trigger - D("0.10"), ask=trigger + D("0.10"),
    )


def test_daic_like_runner_is_recognized_below_old_rvol_threshold():
    decision = evaluate_candidate(observation(symbol="DAIC", change="108", rvol="1.4"))
    assert decision.qualified is True
    assert "relative_volume" in decision.passed_rules
    assert decision.participation_quality.value == "MODERATE"


def test_aehl_like_runner_is_retained_with_weak_participation_and_marginal_spread():
    decision = evaluate_candidate(
        observation(symbol="AEHL", change="43.28", rvol="0.7", spread="2"),
    )
    assert decision.qualified is True
    assert decision.participation_quality.value == "WEAK"
    assert decision.execution_quality is ExecutionQuality.MARGINAL


def test_kxin_is_recognized_before_two_x_and_priority_rises_smoothly():
    early = evaluate_candidate(observation(symbol="KXIN", change="60", rvol="1.2"))
    later = evaluate_candidate(observation(symbol="KXIN", change="60", rvol="2.2"))
    assert early.qualified and later.qualified
    assert later.metrics.rvol_score > early.metrics.rvol_score
    assert later.metrics.momentum_priority > early.metrics.momentum_priority


def test_unavailable_rvol_reference_is_neutral_not_an_opportunity_rejection():
    decision = evaluate_candidate(replace(
        observation(symbol="UNKNOWN_RVOL"),
        average_30_day_volume=D("0"),
    ))
    assert decision.qualified is True
    assert decision.metrics.relative_volume_available is False
    assert decision.participation_quality.value == "UNKNOWN"


def test_malformed_market_and_stale_provider_data_remain_fail_closed():
    with pytest.raises(ValueError):
        evaluate_candidate(replace(observation(), bid=D("0")))
    runtime = WarriorMomentumRuntime(WarriorMomentumConfig(adaptive_context_enabled=True))
    assert runtime.current_execution_liquidity_ok(candidate(spread="0.5"), quote_fresh=False) is False


def test_velocity_attention_uses_cents_and_percent_not_an_absolute_buy_rule():
    low = velocity_attention(D("0.02"), D("0.02"))
    five = velocity_attention(D("0.05"), D("1"))
    ten = velocity_attention(D("0.10"), D("2"))
    expensive_ten = velocity_attention(D("0.10"), D("0.10"))
    assert low < five < ten
    assert expensive_ten < ten


def test_high_priority_mailbox_work_runs_first_without_duplicate_work():
    mailbox = LatestEvaluationMailbox(capacity=4)
    mailbox.enqueue("SLOW", 1, NOW, object(), priority=0)
    mailbox.enqueue("FAST", 1, NOW, object(), priority=2)
    mailbox.enqueue("FAST", 2, NOW + timedelta(milliseconds=1), object(), priority=2)
    assert mailbox.pop().symbol == "FAST"
    assert mailbox.pop().symbol == "SLOW"
    assert mailbox.pop() is None


def test_spread_improvement_preserves_structure_and_enables_execution_quality():
    runtime = WarriorMomentumRuntime(WarriorMomentumConfig(adaptive_context_enabled=True))
    poor = candidate(spread="4")
    tight = candidate(spread="0.5")
    assert runtime.entry_signal(poor) is not None
    assert runtime.entry_signal(tight) is not None
    assert entry_rejections(poor, runtime.config) == ()
    assert runtime.current_execution_liquidity_ok(poor, quote_fresh=True) is False
    assert runtime.current_execution_liquidity_ok(tight, quote_fresh=True) is True
    assert poor.setup == tight.setup


@pytest.mark.parametrize(("spread", "quality"), (
    ("0.5", ExecutionQuality.EXCELLENT),
    ("1.5", ExecutionQuality.GOOD),
    ("3", ExecutionQuality.MARGINAL),
    ("8", ExecutionQuality.TEMPORARILY_BLOCKED),
))
def test_valid_ordinary_spreads_remain_opportunities_with_adaptive_quality(
    spread, quality,
):
    decision = evaluate_candidate(observation(spread=spread))
    assert decision.qualified is True
    assert decision.execution_quality is quality
    assert "spread" in decision.passed_rules


def test_spread_improvement_does_not_require_opportunity_rediscovery():
    poor = evaluate_candidate(observation(symbol="SAIQ", spread="8"))
    marginal = evaluate_candidate(observation(symbol="SAIQ", spread="2"))
    good = evaluate_candidate(observation(symbol="SAIQ", spread="0.8"))
    assert all(item.qualified for item in (poor, marginal, good))
    assert poor.execution_quality is ExecutionQuality.TEMPORARILY_BLOCKED
    assert marginal.execution_quality is ExecutionQuality.MARGINAL
    assert good.execution_quality is ExecutionQuality.GOOD


def test_low_rvol_does_not_destroy_valid_structure_or_signal():
    runtime = WarriorMomentumRuntime()
    value = candidate(spread="0.5", rvol="0.3")
    assert runtime.entry_signal(value) is not None
    assert ReasonCode.RVOL_LOW not in entry_rejections(value, runtime.config)


def test_pipeline_motion_is_local_and_does_not_call_provider():
    adapter = SimpleNamespace()
    pipeline = MomentumScannerPipeline(adapter)
    first = evaluate_candidate(observation(symbol="MOVE", change="20"))
    second = evaluate_candidate(replace(
        observation(symbol="MOVE", change="22"),
        timestamp=NOW + timedelta(seconds=60),
        price=D("5.10"),
    ))
    pipeline._apply_price_motion(first)
    moved = pipeline._apply_price_motion(second)
    assert moved.metrics.price_velocity_cents_1m == D("0.10")
    assert moved.metrics.price_velocity_percent_1m == D("2")
    assert moved.metrics.reevaluation_cadence_ms <= 250

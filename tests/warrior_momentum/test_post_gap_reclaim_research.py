from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.strategies.warrior_momentum.models import MinuteBar
from app.strategies.warrior_momentum.post_gap_reclaim_research import (
    PostGapCandidateContext,
    PostGapReclaimState,
    ResearchOutcomeState,
    detect_post_gap_reclaim,
    evaluate_frozen_plan,
)
from app.strategies.warrior_momentum.post_gap_reclaim_dataset import analyze_capture
from pathlib import Path
from app.strategies.warrior_momentum.setups import detect_best_setup, detect_micro_pullback
from app.strategies.warrior_momentum.models import SetupState


def _bar(minute: int, open_: str, high: str, low: str, close: str, volume: str) -> MinuteBar:
    return MinuteBar(
        "AEMD",
        datetime(2026, 8, 28, 18, minute, tzinfo=UTC),
        *(Decimal(value) for value in (open_, high, low, close, volume)),
    )


def _aemd_bars() -> tuple[MinuteBar, ...]:
    pre = (
        _bar(10, "2.690", "2.700", "2.690", "2.700", "2682"),
        _bar(11, "2.695", "2.710", "2.690", "2.705", "25843"),
        _bar(12, "2.701", "2.710", "2.700", "2.705", "3353"),
        _bar(13, "2.705", "2.705", "2.680", "2.700", "11525"),
        _bar(14, "2.705", "2.710", "2.700", "2.700", "13288"),
        _bar(15, "2.708", "2.710", "2.700", "2.705", "7507"),
        _bar(16, "2.705", "2.710", "2.700", "2.705", "6589"),
    )
    post = (
        _bar(47, "2.671", "2.671", "2.512", "2.530", "132248"),
        _bar(48, "2.540", "2.550", "2.510", "2.550", "54319"),
        _bar(49, "2.540", "2.545", "2.510", "2.530", "50806"),
        _bar(50, "2.525", "2.539", "2.519", "2.539", "16349"),
        _bar(51, "2.540", "2.560", "2.532", "2.560", "36510"),
        _bar(52, "2.565", "2.590", "2.551", "2.569", "28644"),
    )
    return pre + post


def _context() -> PostGapCandidateContext:
    return PostGapCandidateContext(
        momentum_qualified=True,
        percentage_change=Decimal("16.35"),
        relative_volume=Decimal("72"),
        dollar_volume=Decimal("110000000"),
        spread_percent=Decimal("0.40"),
        float_shares=Decimal("711136"),
        distance_from_hod_percent=Decimal("8.9"),
    )


def test_aemd_three_to_six_bar_latency_and_frozen_plan() -> None:
    bars = _aemd_bars()
    detections = [detect_post_gap_reclaim(bars[:7 + count], _context()) for count in range(3, 7)]
    assert [item.state for item in detections] == [
        PostGapReclaimState.SETUP_FORMING,
        PostGapReclaimState.SETUP_FORMING,
        PostGapReclaimState.SETUP_TRIGGERED,
        PostGapReclaimState.TRIGGER_ALREADY_CROSSED,
    ]
    plans = [item.plan for item in detections]
    assert all(plan is not None for plan in plans[:3])
    assert {(plan.trigger, plan.stop, plan.risk_per_share) for plan in plans[:3]} == {
        (Decimal("2.551275"), Decimal("2.510"), Decimal("0.041275"))
    }
    assert detections[0].flush_percent == Decimal("7.380073800738007380073800738")
    assert detections[0].execution_authorized is False


def test_aemd_frozen_outcome_is_future_only_and_non_executable() -> None:
    bars = _aemd_bars()
    detection = detect_post_gap_reclaim(bars[:10], _context())
    outcome = evaluate_frozen_plan(detection, bars[10:])
    assert outcome.state is ResearchOutcomeState.OPEN
    assert outcome.entry_time == bars[11].timestamp
    assert outcome.stop_time is None
    assert outcome.mfe == Decimal("0.038725")
    assert outcome.mae == Decimal("0.019275")
    assert outcome.reached_1r is False
    assert outcome.conservative_stop_first is False
    with pytest.raises(ValueError, match="strictly later"):
        evaluate_frozen_plan(detection, bars[9:])


def test_stop_first_and_trigger_already_crossed_never_retroactively_enter() -> None:
    bars = _aemd_bars()
    detection = detect_post_gap_reclaim(bars[:12], _context())
    assert detection.state is PostGapReclaimState.SETUP_TRIGGERED
    ambiguous = MinuteBar(
        "AEMD", bars[10].timestamp,
        Decimal("2.56"), Decimal("2.60"), Decimal("2.50"), Decimal("2.58"), Decimal("1000"),
    )
    # The trigger bar is already known, and a future bar touching the stop is
    # conservatively a stop before any target credit.
    outcome = evaluate_frozen_plan(detection, (bars[11], ambiguous))
    assert outcome.state is ResearchOutcomeState.STOPPED
    assert outcome.stop_time == ambiguous.timestamp
    assert outcome.conservative_stop_first is True

    too_late = detect_post_gap_reclaim(bars, _context())
    assert too_late.state is PostGapReclaimState.TRIGGER_ALREADY_CROSSED
    assert evaluate_frozen_plan(too_late, (ambiguous,)).state is ResearchOutcomeState.NO_ENTRY


def test_failed_pattern_and_discontinuity_are_rejected() -> None:
    bars = list(_aemd_bars())
    bars[9] = MinuteBar("AEMD", bars[9].timestamp, Decimal("2.525"), Decimal("2.539"), Decimal("2.510"), Decimal("2.510"), Decimal("16349"))
    failed = detect_post_gap_reclaim(tuple(bars[:10]), _context())
    assert failed.state is PostGapReclaimState.SETUP_GEOMETRY_INVALID
    assert any(rule.rule == "support_close" and not rule.passed for rule in failed.rules)

    contiguous = _aemd_bars()[:3]
    no_gap = detect_post_gap_reclaim(contiguous, _context())
    assert no_gap.state is PostGapReclaimState.SETUP_GEOMETRY_INVALID
    insufficient = detect_post_gap_reclaim(_aemd_bars()[:9], _context())
    assert insufficient.state is PostGapReclaimState.INSUFFICIENT_CONTIGUOUS_BARS


def test_research_module_does_not_change_production_selection_or_micro_pullback() -> None:
    bars = _aemd_bars()[7:]
    assert detect_micro_pullback(bars).state is SetupState.NOT_FORMED
    assert detect_best_setup(bars) is None
    source = open("app/strategies/warrior_momentum/post_gap_reclaim_research.py", encoding="utf-8").read()
    assert "broker" not in source.lower()
    assert "import broker" not in source.lower()
    assert "import order" not in source.lower()


def test_future_bars_cannot_change_frozen_geometry_and_capture_is_read_only() -> None:
    bars = _aemd_bars()
    detection = detect_post_gap_reclaim(bars[:10], _context())
    assert detection.plan is not None
    original = detection.plan
    altered_future = MinuteBar(
        "AEMD", bars[10].timestamp,
        Decimal("9"), Decimal("12"), Decimal("8"), Decimal("11"), Decimal("999999"),
    )
    # Future bars are outcome inputs only; they cannot alter the detector plan.
    assert detection.plan == original
    assert detect_post_gap_reclaim(bars[:10] + (altered_future,), _context()).plan == original
    assert altered_future.timestamp > original.created_after_bar
    path = Path("data/warrior_momentum_v1_forward/forward_capture.sqlite3")
    if path.exists():
        before = path.stat().st_size
        opportunities = analyze_capture(path)
        after = path.stat().st_size
        assert before == after
        assert any(item.symbol == "AEMD" for item in opportunities)

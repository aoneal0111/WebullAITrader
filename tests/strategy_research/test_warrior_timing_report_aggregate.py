"""Offline Warrior timing-report aggregator regression."""
import json

import pytest

from app.strategy_research.warrior_timing_report_aggregate import aggregate


def emit(root, symbol, observations, *, bars=4):
    result = {
        "scope": "WARRIOR_MICRO_SELECTION_OFFLINE_V1",
        "symbol": symbol, "day": "2026-07-17", "bars": bars,
        "gap_boundaries": 1,
        "micro_states": {"UNKNOWN": 1, "NOT_FORMED": bars - 1 - len(observations), "FORMING": 0, "TRIGGERED": len(observations)},
        "observations": observations,
    }
    path = root / f"{symbol}_2026-07-17-timing.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    return path


def observation(at, premium, selected="MICRO_PULLBACK"):
    return {
        "at": at,
        "selected_type": selected,
        "trigger_premium": premium,
    }


def test_aggregate_counts_distinct_runs_not_trade_fills(tmp_path):
    emit(tmp_path, "AAOG", [
        observation("2026-07-17T13:31:00+00:00", "0"),
        observation("2026-07-17T13:32:00+00:00", "-0.02", "BULL_FLAG"),
        observation("2026-07-17T13:37:00+00:00", "0.01", "HIGH_OF_DAY_BREAKOUT"),
    ])
    result = aggregate(tmp_path)
    assert result["metrics"]["sessions"] == 1
    assert result["metrics"]["trigger_assessments"] == 3
    assert result["metrics"]["trigger_runs"] == 2
    assert result["trigger_price_comparison"] == {"same": 1, "lower": 1, "higher": 1}
    assert result["orders"] == result["network_requests"] == 0
    assert result["execution_authorized"] is False


def test_rejects_duplicate_or_unordered_report(tmp_path):
    emit(tmp_path, "AAOG", [
        observation("2026-07-17T13:32:00+00:00", "0"),
        observation("2026-07-17T13:31:00+00:00", "0"),
    ])
    with pytest.raises(ValueError, match="TRIGGER_ORDER_INVALID"):
        aggregate(tmp_path)


def test_rejects_empty_or_bad_state_totals(tmp_path):
    with pytest.raises(ValueError, match="REPORT_COUNT_BOUND"):
        aggregate(tmp_path)
    emit(tmp_path, "AAOG", [], bars=5)
    with pytest.raises(ValueError, match="STATE_TOTAL_MISMATCH"):
        aggregate(tmp_path)

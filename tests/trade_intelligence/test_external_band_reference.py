from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.trade_intelligence.knowledge.external_band_reference import sampled_band_reference


OPEN = datetime(2026, 10, 9, 13, 30, tzinfo=UTC)


def rows(closes):
    return [{"symbol": "SPY", "session_open_at": OPEN.isoformat(),
             "completed_at": (OPEN + timedelta(minutes=i)).isoformat(),
             "history_cutoff": (OPEN - timedelta(days=1)).isoformat(),
             "features_available_at": (OPEN + timedelta(minutes=i)).isoformat(),
             "session_open_price": "100", "previous_close_adjusted": "100",
             "sigma_open": "0.01", "vwap": "100", "close": str(close)}
            for i, close in enumerate(closes, 1)]


def test_source_schedule_and_lag_parity():
    data = rows([102] * 30 + [100] * 30 + [98] * 30 + [100])
    original = deepcopy(data)
    result = sampled_band_reference(data)
    actual = [r["exposure_for_interval_just_completed"] for r in result["records"]]
    # Signals at completed minutes 30/60/90 affect only subsequent intervals.
    assert actual == [0] * 30 + [1] * 30 + [0] * 30 + [-1]
    assert result["records"][59]["long_exit_observed"] is True
    assert result["candidate_pnl"] is None
    assert data == original


def test_vwap_can_end_exposure_even_above_band():
    data = rows([102, 102])
    data[1]["vwap"] = "103"
    result = sampled_band_reference(data, trade_frequency=1)
    assert result["records"][1]["long_exit_observed"] is True


def test_equality_is_neutral_and_not_a_stop_fill():
    result = sampled_band_reference(rows([102, 101]), trade_frequency=1)
    assert result["records"][1]["target_exposure"] == 0
    assert result["candidate_pnl"] is None


def test_band_can_fall_without_being_a_ratcheting_stop():
    data = rows([103, 102])
    data[0]["sigma_open"] = "0.02"
    result = sampled_band_reference(data, trade_frequency=1)
    assert result["records"][0]["upper_band"] == "102.00"
    assert result["records"][1]["upper_band"] == "101.00"
    assert result["records"][1]["target_exposure"] == 1


def test_missing_minute_does_not_forward_fill_through_unknown_path():
    data = rows([102, 102, 102])
    del data[1]
    result = sampled_band_reference(data, trade_frequency=1)
    assert result["status"] == "UNRESOLVED_MISSING_OR_UNORDERED_MINUTES"
    assert len(result["records"]) == 1


def test_missing_feature_does_not_fabricate_exit():
    data = rows([102, 102])
    data[1]["sigma_open"] = None
    result = sampled_band_reference(data, trade_frequency=1)
    assert result["status"] == "UNRESOLVED_FEATURE_COVERAGE"
    assert len(result["records"]) == 1


@pytest.mark.parametrize("field", ["history_cutoff", "features_available_at"])
def test_future_features_rejected(field):
    data = rows([102])
    data[0][field] = (OPEN + timedelta(minutes=2)).isoformat()
    with pytest.raises(ValueError, match="FUTURE_FEATURE_EVIDENCE"):
        sampled_band_reference(data, trade_frequency=1)


def test_sessions_and_symbols_cannot_share_positions():
    data = rows([102, 102])
    data[1]["symbol"] = "QQQ"
    with pytest.raises(ValueError, match="MIXED_SYMBOL_OR_SESSION"):
        sampled_band_reference(data, trade_frequency=1)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1"])
def test_invalid_features_rejected(value):
    data = rows([102])
    data[0]["sigma_open"] = value
    with pytest.raises(ValueError, match="INVALID_FEATURE_VALUE"):
        sampled_band_reference(data, trade_frequency=1)

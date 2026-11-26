"""Offline startup five-minute SLA and entry evidence regression."""
from datetime import datetime, timezone, timedelta

import pytest

from app.strategy_research.atlas_startup_entry_health import audit, startup_review, entry_review


START = datetime(2026, 11, 25, 13, 0, tzinfo=timezone.utc)


def snapshot(ready_seconds=None, *, metrics=False):
    values = {
        "process_started_at": START.isoformat(),
        "gui_ready_at": (START + timedelta(seconds=12)).isoformat(),
        "runtime_started_at": (START + timedelta(seconds=13)).isoformat(),
        "reference_warmup_started_at": (START + timedelta(seconds=15)).isoformat(),
    }
    if ready_seconds is not None:
        values["feed_healthy_at"] = (START + timedelta(seconds=ready_seconds)).isoformat()
    if metrics:
        return {"metrics": {"startup_" + k: v for k, v in values.items()}}
    return {"startup": values}


@pytest.mark.parametrize("seconds,status", [(30, "PASSED"), (300, "PASSED"), (301, "EXCEEDED")])
def test_startup_sla_requires_feed_healthy(seconds, status):
    row = startup_review(snapshot(seconds))
    assert row["status"] == status
    assert row["observed_seconds"] == seconds
    assert row["ready_basis"] == "feed_healthy"
    assert row["gui_status"] == "PASSED"
    assert row["gui_observed_seconds"] == 12


def test_missing_feed_is_never_success_even_with_gui_ready():
    no_feed = snapshot()
    assert startup_review(no_feed)["status"] == "INCOMPLETE"
    assert startup_review(no_feed, checked_at=(START + timedelta(seconds=301)).isoformat())["status"] == "EXCEEDED"


def test_supports_flattened_existing_metrics_and_refuses_naive_datetime():
    assert startup_review(snapshot(250, metrics=True))["status"] == "PASSED"
    payload = snapshot(250)
    payload["startup"]["feed_healthy_at"] = "2026-11-25T13:04:10"
    with pytest.raises(ValueError, match="AWARE"):
        startup_review(payload)


def test_clock_inversion_fails_closed():
    row = snapshot(50)
    row["startup"]["feed_healthy_at"] = (START - timedelta(seconds=2)).isoformat()
    with pytest.raises(ValueError, match="STARTUP_CLOCK_ORDER_INVALID"):
        startup_review(row)


def test_entry_stage_counts_not_fake_conversion():
    data = {
        "entry_conversion": {
            "funnel_counts": {
                "SCANNER_QUALIFIED": 100,
                "SETUP_TRIGGERED": 25,
                "ORDER_SUBMITTED": 0,
            },
            "execution_gate_counters": {"QUOTE_REJECTED_BID_STALE": 23},
        },
    }
    output = entry_review(data)
    assert output["stage_counts"]["ORDER_SUBMITTED"] == 0
    assert output["execution_gate_counters"]["QUOTE_REJECTED_BID_STALE"] == 23
    assert "different denominators" in output["unproven"]
    assert "conversion_rate" not in output


def test_missing_source_cannot_count_as_healthy_entry_path():
    result = audit(snapshot(25))
    assert result["entry"]["status"] == "NO_ENTRY_CONVERSION_EVIDENCE"
    assert result["startup"]["status"] == "PASSED"
    assert not result["runtime_modified"] and result["orders"] == 0


def test_negative_or_non_integer_funnel_rejected():
    for value in (-1, 2.2, True):
        with pytest.raises(ValueError, match="INVALID_ENTRY_FUNNEL_COUNTER"):
            entry_review({"entry_conversion": {"funnel_counts": {"ORDER_SUBMITTED": value}}})


def test_slow_gui_is_failure_even_when_feed_missing():
    data = snapshot()
    data["startup"]["gui_ready_at"] = (START + timedelta(seconds=310)).isoformat()
    result = startup_review(data)
    assert result["gui_status"] == "EXCEEDED"
    assert result["status"] == "INCOMPLETE"


def test_durable_snapshot_top_level_run_start_recovers_baseline_not_readiness():
    from app.strategy_research.atlas_startup_entry_health import startup_review

    # This is the actual shape written by PerformanceDiagnostics.
    data = {
        "process_started_at": START.isoformat(),
        "startup": {
            "process_started_at": None,
            "runtime_started_at": (START + timedelta(seconds=10)).isoformat(),
            "gui_ready_at": None,
            "feed_healthy_at": None,
        },
    }
    result = startup_review(data)
    assert result["start_basis"] == "durable_run_start"
    assert result["status"] == "INCOMPLETE"
    assert result["gui_status"] == "INCOMPLETE"
    assert result["observed_seconds"] is None
    assert result["gui_observed_seconds"] is None


def test_durable_run_start_does_not_override_captured_process_stage():
    from app.strategy_research.atlas_startup_entry_health import startup_review

    data = snapshot(240)
    data["process_started_at"] = (START - timedelta(seconds=60)).isoformat()
    result = startup_review(data)
    assert result["start_basis"] == "startup_process_started"
    assert result["observed_seconds"] == 240


def test_observed_gui_stage_can_be_measured_from_durable_run_start():
    from app.strategy_research.atlas_startup_entry_health import startup_review

    data = {"process_started_at": START.isoformat(),
            "startup": {"gui_ready_at": (START + timedelta(seconds=310)).isoformat()}}
    result = startup_review(data)
    assert result["gui_status"] == "EXCEEDED"
    assert result["gui_observed_seconds"] == 310
    assert result["status"] == "INCOMPLETE"

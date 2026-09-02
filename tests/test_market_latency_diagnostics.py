from app.performance_diagnostics import PerformanceDiagnostics
from app.trade_intelligence.models import WorkerMetrics


def test_latency_diagnostics_expose_bounded_queues_rates_and_percentiles() -> None:
    diagnostics = PerformanceDiagnostics()
    diagnostics.record_market_event_callback(1)
    diagnostics.record_market_event_callback(7)
    diagnostics.set_callback_queue_depth(3)
    for age in (10.0, 20.0, 30.0, 40.0, 100.0):
        diagnostics.record_event_processing_age(age)
    diagnostics.set_research_queue_depth(4)
    diagnostics.set_research_queue_depth(9)
    diagnostics.set_research_queue_depth(2)
    diagnostics.record_research_worker_lag(250.0)
    diagnostics.record_scanner_duration(3.0)
    diagnostics.record_experiment_capture_duration(0.4)
    diagnostics.record_observer_duration(2.0)

    snapshot = diagnostics.snapshot()
    assert snapshot.market_event_callbacks == 2
    assert snapshot.callback_queue_depth == 3
    assert snapshot.callback_queue_high_water == 7
    assert snapshot.event_processing_age_p50_ms == 30.0
    assert snapshot.event_processing_age_p90_ms == 40.0
    assert snapshot.event_processing_age_p99_ms == 40.0
    assert snapshot.event_processing_age_max_ms == 100.0
    assert snapshot.research_queue_depth == 2
    assert snapshot.research_queue_high_water == 9
    assert snapshot.research_worker_lag_max_ms == 250.0
    assert snapshot.scanner_duration_max_ms == 3.0
    assert snapshot.experiment_capture_duration_max_ms == 0.4
    assert snapshot.observer_duration_max_ms == 2.0


def test_trade_intelligence_metrics_are_distinct_and_in_memory_only() -> None:
    diagnostics = PerformanceDiagnostics()
    diagnostics.set_research_queue_depth(9)
    diagnostics.set_trade_intelligence_enabled(True)
    diagnostics.update_trade_intelligence(WorkerMetrics(
        accepted=31, checkpointed=30, started=29, completed=27,
        suppressed_duplicate=2, rejected=3, failed=2, outstanding=2,
        queue_depth=1, queue_high_water=7, pressure_episodes=4,
        pressure_recoveries=3, active_outcomes=5, accepting=True,
        worker_lag_p50_ms=11, worker_lag_p90_ms=22,
        worker_lag_p99_ms=33, worker_lag_max_ms=44,
        experiences_created=6, decisions_recorded=8, outcomes_completed=13,
    ))
    snapshot = diagnostics.snapshot()
    assert snapshot.research_queue_depth == 9
    assert snapshot.trade_intelligence_enabled
    assert snapshot.trade_intelligence_experiences_created == 6
    assert snapshot.trade_intelligence_decisions_recorded == 8
    assert snapshot.trade_intelligence_outcomes_completed == 13
    assert snapshot.trade_intelligence_queue_depth == 1
    assert snapshot.trade_intelligence_queue_high_water == 7
    assert snapshot.trade_intelligence_accepted == 31
    assert snapshot.trade_intelligence_completed == 27
    assert snapshot.trade_intelligence_failed == 2
    assert snapshot.trade_intelligence_rejected == 3
    assert snapshot.trade_intelligence_outstanding == 2
    assert snapshot.trade_intelligence_worker_lag_p50_ms == 11
    assert snapshot.trade_intelligence_worker_lag_p90_ms == 22
    assert snapshot.trade_intelligence_worker_lag_p99_ms == 33
    assert snapshot.trade_intelligence_worker_lag_max_ms == 44
    assert snapshot.trade_intelligence_pressure_episodes == 4
    assert snapshot.trade_intelligence_recovery_episodes == 3

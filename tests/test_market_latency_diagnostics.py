from app.performance_diagnostics import PerformanceDiagnostics


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

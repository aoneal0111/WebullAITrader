from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from app.performance_diagnostics import PerformanceDiagnostics
from app.strategies.warrior_momentum.forward_runtime import (
    WarriorForwardCaptureService,
    _PaperState,
)
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.autonomous_paper import (
    lifecycle_identity, opportunity_identity,
)
from app.trade_intelligence.opportunity_memory import OpportunityMemoryState
from tests.warrior_momentum.test_forward_capture import account, point


def _service(tmp_path, replacer, working_entry_source=None):
    store = ForwardCaptureStore(tmp_path / "capture.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer, paper_entry_replacer=replacer,
        paper_working_entry_source=working_entry_source,
    )
    return service, writer


def _prepare_signal(service, value):
    candidate = service.runtime.discover(
        value.observation, value.bars, session=value.session,
    )
    assessed, signal = service.runtime.assess_entry(candidate)
    assert signal is not None
    service.runtime.discover = lambda *_args, **_kwargs: candidate
    service.runtime.assess_entry = lambda _candidate: (assessed, None)
    service.runtime.technical_entry_signal = lambda _candidate: None
    service._paper[signal.symbol] = _PaperState(
        signal, signal.entry_trigger, 100, 100, signal.stop_price,
        50, 25,
        risk_budget=Decimal("50"),
    )
    return candidate, assessed, signal


def test_working_entry_is_pursued_when_current_signal_disappears(tmp_path):
    calls = []
    diagnostics = PerformanceDiagnostics()
    import app.strategies.warrior_momentum.forward_runtime as runtime_module
    original = runtime_module.performance_diagnostics
    runtime_module.performance_diagnostics = diagnostics

    def replacer(**kwargs):
        calls.append(kwargs)

    service, writer = _service(
        tmp_path, replacer,
        working_entry_source=lambda _symbol, _lifecycle: True,
    )
    try:
        value = point(
            observation=replace(point().observation, price=Decimal("10.30"), bid=Decimal("10.28"), ask=Decimal("10.30")),
            best_bid_size=Decimal("200"), best_ask_size=Decimal("100"),
            processing_age_seconds=Decimal("0"), delivery_age_seconds=Decimal("0"),
        )
        _candidate, _assessed, signal = _prepare_signal(service, value)

        service.observe(value, account=account())

        assert calls
        metrics = diagnostics.entry_conversion_metrics()
        assert metrics["pursuit_evaluations"]
        assert metrics["pursuit_evaluations"][-1]["signal_source"] == "RETAINED_WORKING_LIFECYCLE"
        assert metrics["pursuit_evaluations"][-1]["thesis_valid"] is True
        assert calls[0]["lifecycle_id"] == lifecycle_identity(signal)
    finally:
        runtime_module.performance_diagnostics = original
        writer.close()


def test_explicit_opportunity_invalidation_stops_retained_pursuit(tmp_path):
    calls = []
    diagnostics = PerformanceDiagnostics()
    import app.strategies.warrior_momentum.forward_runtime as runtime_module
    original = runtime_module.performance_diagnostics
    runtime_module.performance_diagnostics = diagnostics

    service, writer = _service(
        tmp_path, lambda **kwargs: calls.append(kwargs),
        working_entry_source=lambda _symbol, _lifecycle: True,
    )
    try:
        value = point(
            observation=replace(point().observation, price=Decimal("10.30"), bid=Decimal("10.28"), ask=Decimal("10.30")),
            best_bid_size=Decimal("200"), best_ask_size=Decimal("100"),
            processing_age_seconds=Decimal("0"), delivery_age_seconds=Decimal("0"),
        )
        _candidate, _assessed, signal = _prepare_signal(service, value)
        opportunity_id = opportunity_identity(signal)
        service._memory_opportunity_ids[signal.symbol] = opportunity_id
        service.opportunity_memory.observe(
            opportunity_id=opportunity_id, symbol=signal.symbol,
            trading_date=signal.timestamp.date(), observed_at=signal.timestamp,
        )
        service.opportunity_memory.invalidate(
            signal.timestamp.date(), signal.symbol, opportunity_id,
            signal.timestamp, "THESIS_INVALIDATED",
        )

        service.observe(value, account=account())

        assert calls == []
        evaluations = diagnostics.entry_conversion_metrics()["pursuit_evaluations"]
        assert evaluations[-1]["evaluation_result"] == "THESIS_INVALID"
        assert evaluations[-1]["signal_source"] == "RETAINED_WORKING_LIFECYCLE"
        assert evaluations[-1]["thesis_valid"] is False
    finally:
        runtime_module.performance_diagnostics = original
        writer.close()


def test_completed_entry_is_not_treated_as_working_lifecycle(tmp_path):
    calls = []
    diagnostics = PerformanceDiagnostics()
    import app.strategies.warrior_momentum.forward_runtime as runtime_module
    original = runtime_module.performance_diagnostics
    runtime_module.performance_diagnostics = diagnostics

    service, writer = _service(
        tmp_path, lambda **kwargs: calls.append(kwargs),
        working_entry_source=lambda _symbol, _lifecycle: False,
    )
    try:
        value = point(
            observation=replace(point().observation, price=Decimal("10.30"), bid=Decimal("10.28"), ask=Decimal("10.30")),
            best_bid_size=Decimal("200"), best_ask_size=Decimal("100"),
            processing_age_seconds=Decimal("0"), delivery_age_seconds=Decimal("0"),
        )
        _prepare_signal(service, value)
        service.observe(value, account=account())
        assert calls == []
        assert diagnostics.entry_conversion_metrics()["counters"][
            "pursuit_not_invoked_due_to_state"
        ] == 1
    finally:
        runtime_module.performance_diagnostics = original
        writer.close()

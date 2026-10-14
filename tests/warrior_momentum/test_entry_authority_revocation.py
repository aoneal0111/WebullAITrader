from threading import Event, Thread
from types import SimpleNamespace

import pytest

from app.strategies.warrior_momentum import WarriorCaptureHealth, WarriorDesktopSidecar
from tests.warrior_momentum.test_desktop_sidecar import (
    T0, RecordingQuickScalper, adapter, deliver, quote, trade,
)


def test_terminal_stream_failure_survives_health_recovery_and_keeps_protection(tmp_path, monkeypatch):
    scanner, scalper = adapter(), RecordingQuickScalper()
    sidecar = WarriorDesktopSidecar(
        enabled=True, storage_path=tmp_path / "stream.sqlite3", clock=lambda: T0,
        quick_scalper_observer=scalper, paper_position_quantity_source=lambda _: 10,
    )
    sidecar.bind_scanner_adapter(scanner)
    sidecar.start("PAPER")
    repaired = []
    monkeypatch.setattr(sidecar._service, "reconcile_authoritative_protection",
                        lambda symbol, at: repaired.append(symbol) or True)
    try:
        deliver(scanner, sidecar, quote())
        deliver(scanner, sidecar, trade(2, T0, "10.20"))
        assert sidecar.snapshot().entry_authorized
        sidecar.fail_closed_market_data()
        scalper.calls.clear()
        deliver(scanner, sidecar, quote())
        sidecar.authoritative_observe(quote())
        assert repaired == ["XYZ"]
        assert sidecar.snapshot().health is WarriorCaptureHealth.DEGRADED
        assert not sidecar.snapshot().entry_authorized
        assert scalper.calls and not any(call[4] for call in scalper.calls)
        old_service = sidecar._service
        sidecar.start("PAPER")
        assert sidecar._service is old_service
        assert not sidecar.snapshot().entry_authorized
        sidecar.stop()
        sidecar.start("PAPER")
        assert sidecar.snapshot().entry_authorized
    finally:
        sidecar.stop()


def test_inflight_callback_cannot_restore_terminally_revoked_entries(tmp_path, monkeypatch):
    sidecar = WarriorDesktopSidecar(
        enabled=True, storage_path=tmp_path / "inflight.sqlite3", clock=lambda: T0,
    )
    sidecar.start("PAPER")
    try:
        monkeypatch.setattr(sidecar, "_consume", lambda _: sidecar.fail_closed_market_data())
        sidecar(quote())
        assert not sidecar.snapshot().entry_authorized
        assert sidecar.snapshot().health is WarriorCaptureHealth.DEGRADED
    finally:
        sidecar.stop()


def test_stop_revocation_survives_inflight_health_check(tmp_path, monkeypatch):
    sidecar = WarriorDesktopSidecar(
        enabled=True, storage_path=tmp_path / "stop.sqlite3", clock=lambda: T0,
    )
    sidecar.start("PAPER")
    notified = Event()
    failures = []
    def stop():
        try:
            sidecar.stop()
        except Exception as exc:
            failures.append(exc)
    worker = Thread(target=stop)
    sidecar._processing_lock.acquire()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sidecar, "_grok_native_producer", SimpleNamespace(request_stop=notified.set))
            patch.setattr(sidecar, "_stop_impl", lambda: None)
            worker.start()
            assert notified.wait(2)
            sidecar._update_health(allow_recovery=True)
            assert not sidecar.snapshot().entry_authorized
            sidecar._processing_lock.release()
            worker.join(2)
            assert not worker.is_alive() and not failures
    finally:
        if sidecar._processing_lock.locked():
            sidecar._processing_lock.release()
        worker.join(2)
        sidecar.stop()


def test_failure_during_startup_cannot_be_overwritten_by_started_state(tmp_path):
    holder = {}
    fail_once = [True]
    def clock():
        if fail_once[0]:
            fail_once[0] = False
            holder["sidecar"].fail_closed_market_data()
        return T0
    sidecar = WarriorDesktopSidecar(
        enabled=True, storage_path=tmp_path / "startup.sqlite3", clock=clock,
    )
    holder["sidecar"] = sidecar
    sidecar.start("PAPER")
    try:
        assert not sidecar.snapshot().entry_authorized
        assert sidecar.snapshot().health is WarriorCaptureHealth.DEGRADED
    finally:
        sidecar.stop()


@pytest.mark.parametrize("binding", ["_grok_native_producer", "_grok_runtime_handoff"])
def test_optional_adviser_stop_failure_cannot_prevent_revocation(tmp_path, monkeypatch, binding):
    sidecar = WarriorDesktopSidecar(
        enabled=True, storage_path=tmp_path / "adviser.sqlite3", clock=lambda: T0,
    )
    sidecar.start("PAPER")
    def failed_notification():
        raise RuntimeError("synthetic adviser failure")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(sidecar, binding, SimpleNamespace(request_stop=failed_notification))
            sidecar.fail_closed_market_data()
            sidecar._update_health(allow_recovery=True)
            assert not sidecar.snapshot().entry_authorized
    finally:
        sidecar.stop()

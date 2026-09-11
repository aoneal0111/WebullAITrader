from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from threading import Event, Thread
from types import SimpleNamespace
from time import monotonic

from app.market_data.models import MarketEvent, MarketEventType, QuotePayload
from app.strategies.warrior_momentum.desktop_sidecar import (
    WarriorCaptureHealth,
    WarriorDesktopSidecar,
)
from app.strategies.warrior_momentum.runtime import WarriorMomentumRuntime


def _quote() -> MarketEvent:
    return MarketEvent(
        1,
        datetime(2026, 9, 11, 8, tzinfo=UTC),
        "DBGI",
        "test",
        MarketEventType.QUOTE,
        QuotePayload(Decimal("7.20"), Decimal("7.21"), Decimal("100"), Decimal("200")),
    )


def _sidecar(tmp_path, quantity, service):
    sidecar = WarriorDesktopSidecar(
        enabled=True,
        storage_path=tmp_path / "capture.jsonl",
        paper_position_quantity_source=lambda symbol: Decimal(quantity),
    )
    sidecar._health = WarriorCaptureHealth.RUNNING
    sidecar._service = service
    sidecar._writer = SimpleNamespace(
        healthy=True,
        metrics=lambda: SimpleNamespace(
            dropped_records=0,
            queue_depth=0,
        ),
    )
    return sidecar


def test_normal_market_event_does_not_reconcile_already_clean_position(tmp_path):
    calls = []
    service = SimpleNamespace(
        reconcile_authoritative_protection=lambda symbol, timestamp: calls.append(symbol),
        runtime=WarriorMomentumRuntime(),
    )
    sidecar = _sidecar(tmp_path, 40, service)
    sidecar._last_protection_quantity["DBGI"] = 40
    sidecar._last_protection_attempt_at["DBGI"] = monotonic()

    sidecar(_quote())

    assert calls == []


def test_quantity_change_reconciles_immediately(tmp_path):
    calls = []
    service = SimpleNamespace(
        reconcile_authoritative_protection=lambda symbol, timestamp: calls.append(symbol) or True,
        runtime=WarriorMomentumRuntime(),
    )
    sidecar = _sidecar(tmp_path, 40, service)

    sidecar(_quote())

    assert calls == ["DBGI"]
    assert "DBGI" not in sidecar._protection_dirty


def test_slow_reconciliation_does_not_hold_sidecar_snapshot_lock(tmp_path):
    started = Event()
    release = Event()

    def reconcile(symbol, timestamp):
        started.set()
        release.wait(5.0)
        return True

    service = SimpleNamespace(
        reconcile_authoritative_protection=reconcile,
        runtime=WarriorMomentumRuntime(),
        open_paper_symbols=(),
        counterfactual_symbols=(),
    )
    sidecar = _sidecar(tmp_path, 40, service)
    worker = Thread(target=sidecar, args=(_quote(),))
    worker.start()
    assert started.wait(1.0)

    snapshot = sidecar.snapshot()
    assert snapshot.health is WarriorCaptureHealth.RUNNING

    release.set()
    worker.join(2.0)
    assert not worker.is_alive()

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
from app.strategies.warrior_momentum.desktop_sidecar import strategy_configuration_fingerprint
from app.strategies.warrior_momentum.forward_models import (
    CaptureRecord, CaptureRecordType, records_with_configuration_fingerprint,
)
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore


NOW = datetime(2026, 9, 9, 14, 30, tzinfo=UTC)


def _record(record_type: CaptureRecordType, payload: dict | None = None) -> CaptureRecord:
    return CaptureRecord.create(record_type, "XYZ", NOW, payload or {})


def test_writer_persists_every_forward_record_with_exact_fingerprint(tmp_path) -> None:
    fingerprint = strategy_configuration_fingerprint()
    store = ForwardCaptureStore(tmp_path / "capture.sqlite3")
    writer = ForwardCaptureWriter(
        store, flush_interval_seconds=0.01,
        configuration_fingerprint=fingerprint,
    )
    try:
        writer.submit_many(tuple(_record(item) for item in CaptureRecordType))
        writer.flush()
        persisted = store.records()
    finally:
        writer.close()

    assert {item.record_type for item in persisted} == set(CaptureRecordType)
    assert {item.payload["configuration_fingerprint"] for item in persisted} == {fingerprint}


def test_configuration_fingerprint_changes_with_effective_policy(tmp_path) -> None:
    old = WarriorMomentumConfig()
    new = replace(
        old,
        discovery=replace(
            old.discovery,
            maximum_price=Decimal("100"),
            minimum_dollar_volume=Decimal("250000"),
        ),
    )
    assert strategy_configuration_fingerprint(old) != strategy_configuration_fingerprint(new)


def test_session_derived_identity_supports_legacy_research_records() -> None:
    fingerprint = "old-policy"
    records = (
        _record(CaptureRecordType.OBSERVATION_SESSION, {
            "action": "START", "configuration_fingerprint": fingerprint,
        }),
        _record(CaptureRecordType.DECISION, {"decision": "WATCH"}),
        _record(CaptureRecordType.OBSERVATION_SESSION, {"action": "END"}),
        _record(CaptureRecordType.DECISION, {"decision": "UNATTRIBUTED"}),
    )
    attributed = tuple(records_with_configuration_fingerprint(records))
    assert attributed[1][1] == fingerprint
    assert attributed[3][1] is None


def test_different_policy_records_are_excluded_from_active_recovery(tmp_path) -> None:
    old_fp = "old-policy"
    new_fp = "new-policy"
    store = ForwardCaptureStore(tmp_path / "recovery.sqlite3")
    old_bar = _record(CaptureRecordType.MINUTE_BAR, {
        "bar_timestamp": NOW.isoformat(), "open": "5", "high": "5",
        "low": "5", "close": "5", "volume": "100",
        "configuration_fingerprint": old_fp,
    })
    old_transition = _record(CaptureRecordType.STATE_TRANSITION, {
        "to": "QUALIFIED", "configuration_fingerprint": old_fp,
    })
    assert store.append_batch((old_bar, old_transition)) == (2, 0)

    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01,
                                  configuration_fingerprint=new_fp)
    try:
        service = WarriorForwardCaptureService(
            store, writer, configuration_fingerprint=new_fp,
        )
        assert service._seen_bars == set()
        assert service._last_transition == {}
    finally:
        writer.close()


def test_unattributed_legacy_records_are_not_active_recovery(tmp_path) -> None:
    store = ForwardCaptureStore(tmp_path / "legacy.sqlite3")
    record = _record(CaptureRecordType.STATE_TRANSITION, {"to": "QUALIFIED"})
    assert store.append_batch((record,)) == (1, 0)
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01,
                                  configuration_fingerprint="current-policy")
    try:
        service = WarriorForwardCaptureService(
            store, writer, configuration_fingerprint="current-policy",
        )
        assert service._last_transition == {}
    finally:
        writer.close()


def test_writer_does_not_mutate_original_record_payload_or_id(tmp_path) -> None:
    store = ForwardCaptureStore(tmp_path / "identity.sqlite3")
    original = _record(CaptureRecordType.DECISION, {"x": 1})
    original_id = original.record_id
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01,
                                  configuration_fingerprint="current-policy")
    try:
        writer.submit(original)
        writer.flush()
    finally:
        writer.close()
    persisted = store.records()[0]
    assert original.record_id == original_id
    assert "configuration_fingerprint" not in original.payload
    assert persisted.record_id != original_id
    assert persisted.payload["configuration_fingerprint"] == "current-policy"

"""Recovery preserves bars and identity without decoding unrelated capture history."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.strategies.warrior_momentum.desktop_sidecar import WarriorDesktopSidecar
from app.strategies.warrior_momentum.forward_models import CaptureRecord, CaptureRecordType
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore

NOW = datetime(2026, 11, 16, 18, tzinfo=UTC)


def record(kind, payload, index=0, symbol='XYZ'):
    return CaptureRecord.create(kind, symbol, NOW + timedelta(minutes=index), payload)


def bar(index, fingerprint=None):
    payload = dict(bar_timestamp=(NOW + timedelta(minutes=index)).isoformat(),
                   open='10', high='11', low='9', close='10', volume='100')
    if fingerprint is not None:
        payload['configuration_fingerprint'] = fingerprint
    return record(CaptureRecordType.MINUTE_BAR, payload, index)


def test_recovery_filters_diagnostics_retains_legacy_identity_and_newest_120(tmp_path):
    sidecar = WarriorDesktopSidecar(enabled=True, storage_path=tmp_path/'capture.sqlite3')
    fingerprint = sidecar.configuration_fingerprint
    store = ForwardCaptureStore(sidecar.storage_path)
    rows = [record(CaptureRecordType.OBSERVATION_SESSION,
                   dict(action='START', configuration_fingerprint=fingerprint))]
    rows.extend(record(CaptureRecordType.LATENCY_DIAGNOSTIC, dict(index=i), i)
                for i in range(3000))
    # Reverse arrival order must not evict newer bars during streaming recovery.
    rows.extend(bar(i) for i in reversed(range(150)))
    rows.append(record(CaptureRecordType.OBSERVATION_SESSION, dict(action='END'), 151))
    rows.append(bar(152))  # unbound legacy bar must not inherit after END
    rows.append(bar(153, 'another-configuration'))
    rows.append(bar(154, fingerprint))  # direct identity remains recoverable
    store.append_batch(tuple(rows))
    sidecar._store = store
    sidecar._restore_bars()
    values = sidecar._bars['XYZ']
    assert len(values) == 120
    assert values[0].timestamp == NOW + timedelta(minutes=31)
    assert values[-1].timestamp == NOW + timedelta(minutes=154)
    assert values[-1].close == Decimal('10')
    assert store.records_materialized_total() == 155
    assert store.count() == 3155

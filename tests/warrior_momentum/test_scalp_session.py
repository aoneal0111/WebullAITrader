from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.market.scalp_session import scalp_entry_deadline, scalp_entry_window
from tests.warrior_momentum.test_quick_scalper_runtime import adapter, snapshot

ET = ZoneInfo('America/New_York')


@pytest.mark.parametrize('at,session,deadline', [
    ('2026-10-08T09:20', 'PREMARKET', '2026-10-08T09:25'),
    ('2026-10-08T15:59:58', 'REGULAR', '2026-10-08T15:55'),
    ('2026-10-08T19:59', 'AFTER_HOURS', '2026-10-08T19:55'),
    ('2026-11-27T12:59', 'REGULAR', '2026-11-27T12:55'),
    ('2026-11-02T15:50', 'REGULAR', '2026-11-02T15:55'),
])
def test_calendar_boundaries_include_early_close_and_dst(at, session, deadline):
    current = datetime.fromisoformat(at).replace(tzinfo=ET).astimezone(UTC)
    actual, until = scalp_entry_window(current)
    assert actual == session
    assert until == datetime.fromisoformat(deadline).replace(tzinfo=ET)


@pytest.mark.parametrize('at', ['2026-10-10T12:00', '2026-12-25T12:00', '2026-10-08T20:00'])
def test_no_entry_window_outside_supported_trading_cycle(at):
    assert scalp_entry_window(datetime.fromisoformat(at).replace(tzinfo=ET)) is None


def test_review_cannot_rearm_old_session_authority():
    before = datetime(2026, 10, 8, 15, 50, tzinfo=ET)
    original = scalp_entry_window(before)[1]
    after = before + timedelta(minutes=20)
    assert scalp_entry_deadline(after, {'scalp_entry_session_deadline': original.isoformat()}) == original


@pytest.mark.parametrize('at', ['2026-10-08T15:59:58', '2026-10-08T16:10'])
def test_runtime_rejects_mane_boundary_and_stale_session_label(at):
    runtime, bridge, quotes = adapter()
    at = datetime.fromisoformat(at).replace(tzinfo=ET)
    runtime._clock = lambda: at
    quotes.value = replace(quotes.value, last_timestamp=at, bid_timestamp=at, ask_timestamp=at)
    value = replace(snapshot(), observed_at=at, decision_at=at,
                    last_timestamp=at, bid_timestamp=at, ask_timestamp=at)
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    assert not bridge.entries
    assert quotes.decisions[-1]['rejection_reason'] == 'SCALP_SESSION_WINDOW'


def test_runtime_keeps_stream_setup_and_original_deadline_in_intent():
    runtime, bridge, _ = adapter()
    value = replace(snapshot(), setup_type='QUICK_SCALP_STREAM')
    runtime._snapshots[("FAST", "g1")] = value
    runtime.scalper.observe(value)
    intent = bridge.entries[0][0]
    assert intent.setup_type == 'QUICK_SCALP_STREAM'
    assert intent.entry_session_deadline == datetime(2026, 10, 5, 15, 55, tzinfo=ET)


@pytest.mark.parametrize('metadata', [
    {'scalp_maximum_holding_seconds': -1},
    {'scalp_maximum_holding_seconds': 'broken'},
    {'scalp_entry_session_deadline': '2026-10-08T15:55'},
    {'scalp_entry_session_deadline': 'broken'},
])
def test_corrupt_recovered_entry_metadata_expires_without_stopping_management(metadata):
    at = datetime(2026, 10, 8, 15, 50, tzinfo=ET)
    assert scalp_entry_deadline(at, metadata) == at

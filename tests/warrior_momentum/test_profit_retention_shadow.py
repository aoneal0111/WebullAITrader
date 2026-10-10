from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from app.strategies.warrior_momentum.profit_retention_shadow import ProfitRetentionShadow
from app.strategies.warrior_momentum import ForwardCaptureStore, ForwardCaptureWriter, WarriorForwardCaptureService, CaptureRecordType

T0 = datetime(2026, 10, 9, 15, tzinfo=UTC)


def shadow(complete=True):
    s = ProfitRetentionShadow()
    s.fill(identity="1", side="BUY", quantity=D(10), price=D(10), at=T0, stop=D(9), complete=complete)
    return s


def quote(s, second, bid, **changes):
    values = dict(at=T0 + timedelta(seconds=second), source_at=T0 + timedelta(seconds=second),
                  bid=D(bid), ask=D(bid) + D(".01"), max_age=D(5), max_gap=D(5))
    values.update(changes)
    return s.observe(**values)


def test_arms_then_signals_once_without_execution():
    s = shadow()
    assert quote(s, 1, "10.5")["action"] == "PROFIT_SHADOW_ARMED"
    result = quote(s, 2, "10.2")
    assert result["action"] == "PROFIT_SHADOW_SIGNAL"
    assert result["gross_profit_mark"] == 2
    assert result["execution_enabled"] is False
    assert quote(s, 3, "10.1") is None


def test_future_peak_never_arms_retroactively_and_partials_use_actual_cash():
    s = shadow()
    assert quote(s, 1, "10.1") is None
    assert quote(s, 2, "10") is None
    assert quote(s, 3, "10.5")["action"] == "PROFIT_SHADOW_ARMED"
    s.fill(identity="2", side="SELL", quantity=D(5), price=D(11), at=T0 + timedelta(seconds=4))
    assert quote(s, 5, "10.2") is None
    assert s.peak == 6


@pytest.mark.parametrize("second,changes,reason", [
    (6, {}, "QUOTE_GAP"),
    (1, {"source_at": T0 + timedelta(seconds=2)}, "FUTURE_QUOTE"),
    (1, {"source_at": T0 - timedelta(seconds=5)}, "STALE_QUOTE"),
    (1, {"ask": D(9)}, "INVALID_QUOTE"),
    (0, {}, "NONINCREASING_OBSERVATION"),
])
def test_invalid_path_is_permanently_unavailable(second, changes, reason):
    s = shadow()
    assert quote(s, second, "10.5", **changes)["reason"] == reason
    assert quote(s, second + 1, "11") is None
    assert not s.armed


def test_partial_entry_waits_for_completion_and_duplicate_fill_ignored():
    s = shadow(False)
    assert quote(s, 1, "11") is None
    s.fill(identity="1", side="BUY", quantity=D(10), price=D(10), at=T0, stop=D(9), complete=True)
    assert s.bought == 10
    s.entry_complete = True
    assert quote(s, 2, "10.5")["action"] == "PROFIT_SHADOW_ARMED"
    s.fill(identity="3", side="BUY", quantity=D(1), price=D(10), at=T0 + timedelta(seconds=3), stop=D(9), complete=True)
    assert quote(s, 4, "11")["reason"] == "ENTRY_CHANGED_AFTER_OBSERVATION"


def test_runtime_keeps_subsecond_changed_sides_and_shadow_signals(tmp_path):
    store = ForwardCaptureStore(tmp_path / "capture.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=.01)
    service = WarriorForwardCaptureService(store, writer, paper_campaign_id="paper")
    try:
        service.observe_paper_event(SimpleNamespace(source="paper", sequence=1,
            fill=SimpleNamespace(symbol="XYZ", side="BUY", quantity=D(10), fill_price=D(10), timestamp=T0),
            order=SimpleNamespace(lifecycle_id="WARRIOR_MOMENTUM_V1|XYZ|one", order_id="entry",
                                  structural_stop_price="9", status="FILLED")))
        for seconds, bid in ((.1, "10.5"), (.2, "10.2"), (.3, "10.2")):
            at = T0 + timedelta(seconds=seconds)
            service.observe_execution_quote(symbol="XYZ", quote_timestamp=at, evaluated_at=at,
                                            bid=D(bid), ask=D(bid)+D(".01"), last=None)
        service.observe_execution_quote(symbol="XYZ", quote_timestamp=T0 + timedelta(seconds=.2),
            evaluated_at=T0 + timedelta(seconds=.4), bid=D("10.1"), ask=D("10.11"), last=None)
        writer.flush()
        actions = [r.payload["action"] for r in store.records(record_type=CaptureRecordType.EXECUTION_PRICE_PATH)]
        assert actions.count("QUOTE") == 2
        assert actions.count("PROFIT_SHADOW_ARMED") == 1
        assert actions.count("PROFIT_SHADOW_SIGNAL") == 1
        assert actions.count("PROFIT_SHADOW_UNAVAILABLE") == 1
        assert service._paper == {}
    finally:
        writer.close()


def test_quotes_during_partial_entry_preserve_coverage_without_arming():
    s = shadow(False)
    assert quote(s, 1, "11") is None
    s.fill(identity="2", side="BUY", quantity=D(5), price=D(10), at=T0 + timedelta(seconds=2), stop=D(9), complete=False)
    for second in range(3, 12):
        assert quote(s, second, "11") is None
    assert not s.armed and s.problem is None
    s.entry_complete = True
    assert quote(s, 12, "10.5")["action"] == "PROFIT_SHADOW_ARMED"

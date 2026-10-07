from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.strategies.warrior_momentum.configuration import SetupConfig
from app.strategies.warrior_momentum.detector_diagnostics import BoundedSetupDiagnostics
from app.strategies.warrior_momentum.models import SetupDetection, SetupState, SetupType
from app.strategies.warrior_momentum.setups import AccelerationPoint


def _points(count=3, *, start=None, step_seconds=5):
    start = start or datetime(2026, 9, 30, 14, 0, tzinfo=UTC)
    return tuple(AccelerationPoint(
        start + timedelta(seconds=i * step_seconds), Decimal("5") + Decimal(i) / 100,
        Decimal("1000") + Decimal(i * 100), Decimal("5000") + Decimal(i * 500),
        Decimal("0.3"), True, False, True,
    ) for i in range(count))


def _observe(store, points, timestamp=None, selected=None):
    timestamp = timestamp or points[-1].timestamp
    store.observe("FAST", (), points, session="REGULAR", timestamp=timestamp,
                  config=SetupConfig(), selected_setup=selected)


def test_acceleration_first_failure_and_lifetime_are_distinct():
    store = BoundedSetupDiagnostics()
    points = _points(2)
    _observe(store, points)
    assert store.snapshot("FAST")["FAST"]["MOMENTUM_ACCELERATION"]["first_failed_predicate"] == "INSUFFICIENT_POINTS"
    points = _points(3, step_seconds=20)
    _observe(store, points)
    assert store.snapshot("FAST")["FAST"]["MOMENTUM_ACCELERATION"]["first_failed_predicate"] == "LIFETIME_EXCEEDED"


def test_masking_forming_and_episode_counts_are_observable(monkeypatch):
    import app.strategies.warrior_momentum.detector_diagnostics as module

    forming = SetupDetection(SetupType.BULL_FLAG, SetupState.FORMING, Decimal("70"),
                             Decimal("5.1"), Decimal("4.9"), structural_episode_id="EP1")
    selected = SetupDetection(SetupType.HIGH_OF_DAY_BREAKOUT, SetupState.FORMING, Decimal("80"),
                              Decimal("5.1"), Decimal("4.9"), structural_episode_id="EP2")
    monkeypatch.setattr(module, "detect_bull_flag", lambda *_: forming)
    monkeypatch.setattr(module, "detect_hod_breakout", lambda *_: selected)
    monkeypatch.setattr(module, "detect_flat_top", lambda *_: SetupDetection(SetupType.FLAT_TOP_BREAKOUT, SetupState.NOT_FORMED, Decimal("0")))
    monkeypatch.setattr(module, "detect_micro_pullback", lambda *_: SetupDetection(SetupType.MICRO_PULLBACK, SetupState.NOT_FORMED, Decimal("0")))
    monkeypatch.setattr(module, "detect_momentum_acceleration", lambda *_: SetupDetection(SetupType.MOMENTUM_ACCELERATION, SetupState.NOT_FORMED, Decimal("0")))
    monkeypatch.setattr(module, "detect_momentum_reacceleration", lambda *_: SetupDetection(SetupType.MOMENTUM_REACCELERATION, SetupState.NOT_FORMED, Decimal("0")))
    monkeypatch.setattr(module, "detect_reclaim_continuation", lambda *_: SetupDetection(SetupType.RECLAIM_CONTINUATION, SetupState.NOT_FORMED, Decimal("0")))
    store = BoundedSetupDiagnostics()
    pts = _points(3)
    _observe(store, pts, pts[-1].timestamp, selected=selected)
    detail = store.snapshot("FAST")["FAST"]["BULL_FLAG"]
    assert detail["state"] == "MASKED_BY_STRONGER_SETUP"
    assert detail["first_failed_predicate"] == "MASKED_BY_STRONGER_SETUP"

    triggered = SetupDetection(SetupType.BULL_FLAG, SetupState.TRIGGERED, Decimal("90"),
                               Decimal("5.1"), Decimal("4.9"), structural_episode_id="EP1")
    monkeypatch.setattr(module, "detect_bull_flag", lambda *_: triggered)
    _observe(store, pts, pts[-1].timestamp + timedelta(seconds=1))
    _observe(store, pts, pts[-1].timestamp + timedelta(seconds=2))
    assert store.unique_setup_episodes["BULL_FLAG"] == 1
    assert store.unique_triggered_episodes["BULL_FLAG"] == 1


def test_transition_and_symbol_bounds():
    store = BoundedSetupDiagnostics(maximum_symbols=2, maximum_transitions=2)
    for index, symbol in enumerate(("A", "B", "C", "D")):
        points = _points(3, start=datetime(2026, 9, 30, 14, index, tzinfo=UTC))
        store.observe(symbol, (), points, session="REGULAR", timestamp=points[-1].timestamp,
                      config=SetupConfig(), selected_setup=None)
    assert len(store.snapshot()) == 2
    assert len(store.transitions("FAST")) <= 2


def test_session_reset_clears_previous_symbol_diagnostics():
    store = BoundedSetupDiagnostics()
    points = _points(3)
    _observe(store, points)
    assert store.snapshot("FAST")
    later = _points(3, start=datetime(2026, 10, 1, 14, 0, tzinfo=UTC))
    store.observe("FAST", (), later, session="PREMARKET", timestamp=later[-1].timestamp,
                  config=SetupConfig(), selected_setup=None)
    assert len(store.transitions("FAST")) == 7


def test_actionable_detector_followed_by_not_formed_is_invalidated(monkeypatch):
    import app.strategies.warrior_momentum.detector_diagnostics as module
    forming = SetupDetection(SetupType.BULL_FLAG, SetupState.FORMING, Decimal("70"),
                             Decimal("5.1"), Decimal("4.9"), structural_episode_id="EP1")
    missing = SetupDetection(SetupType.BULL_FLAG, SetupState.NOT_FORMED, Decimal("0"))
    monkeypatch.setattr(module, "detect_bull_flag", lambda *_: forming)
    store = BoundedSetupDiagnostics()
    points = _points(3)
    _observe(store, points, selected=forming)
    monkeypatch.setattr(module, "detect_bull_flag", lambda *_: missing)
    _observe(store, points, timestamp=points[-1].timestamp + timedelta(seconds=1))
    assert store.snapshot("FAST")["FAST"]["BULL_FLAG"]["state"] == "INVALIDATED"

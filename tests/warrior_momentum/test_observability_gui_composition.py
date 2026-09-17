from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app.composition import create_desktop_composition
from app.gui.main_window import MainWindow
from app.gui.models import WatchlistRow, WatchlistSnapshot
from app.gui.pages.dashboard import DashboardPage
from app.gui.widgets.market_workspace import MarketWorkspace
from app.strategies.warrior_momentum.observability import (
    NOOP_WARRIOR_OBSERVABILITY,
    BoundedJsonlWarriorObservabilitySink,
)
class RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[dict[str, object]] = []
        self.fail = fail

    def emit_warrior(self, **event: object) -> None:
        if self.fail:
            raise RuntimeError("diagnostic failure")
        self.events.append(event)

    def close(self) -> None:
        return None


def _scanner(symbol: str = "DAIC", *, selected: bool = True) -> WatchlistRow:
    return WatchlistRow(
        symbol=symbol, selected=selected, latest_price="2.08", change="+0.93",
        change_percent="+80.90%", bid="2.07", ask="2.08", volume="83,800,000",
        market_status="OPEN", last_update="now", stale="LIVE", rank="1",
        score="94.00", relative_volume="48.80x", dollar_volume="$174,304,000",
        spread="0.48%", catalyst="SEC Filing", freshness="LIVE",
        session="REGULAR", classification="QUALIFYING", float_shares="7.84M",
    )


def _warrior(symbol: str = "DAIC") -> WatchlistRow:
    return WatchlistRow(
        symbol=symbol, selected=False, latest_price="2.08", change="--",
        change_percent="+80.90%", bid="--", ask="--", volume="83,800,000",
        market_status="OPEN", last_update="now", stale="LIVE", rank="1",
        score="76.55", session="REGULAR", setup="BULL_FLAG",
        setup_state="FORMING", strategy_status="SETUP_FORMING",
        entry_trigger="2.1000", stop_price="1.9800", blocking_reasons="--",
        warrior_evaluated=True, warrior_score="76.55",
        warrior_status="SETUP_FORMING", warrior_session="REGULAR",
        strategy_name="Warrior Momentum",
    )


def _view(*rows: WatchlistRow):
    return SimpleNamespace(
        focus=WatchlistSnapshot(rows=rows), enabled=True, health="RUNNING",
        summary="", funnel="", research="",
    )


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_dashboard_and_market_workspace_share_explicit_sink(qt_app):
    sink = RecordingSink()
    dashboard = DashboardPage(warrior_observability=sink)
    assert dashboard.market_workspace._warrior_observability is sink

    workspace = dashboard.market_workspace
    workspace.render(WatchlistSnapshot(rows=(_scanner(),)))
    workspace.render_warrior(_view(_warrior()))
    assert any(event["event"] == "GUI_FOCUS_LOOKUP_RESULT" for event in sink.events)


def test_gui_lookup_match_miss_and_no_view_are_observed_without_state_change(qt_app):
    sink = RecordingSink()
    workspace = MarketWorkspace(warrior_observability=sink)
    snapshot = WatchlistSnapshot(rows=(_scanner(),))
    workspace.render(snapshot)
    assert workspace.trade_intelligence._decision.text() == "EVALUATING"
    workspace.render_warrior(_view(_warrior()))
    assert workspace.trade_intelligence._decision.text() == "WAIT"
    workspace.render_warrior(_view(_warrior("OTHER")))
    assert workspace.trade_intelligence._decision.text() == "EVALUATING"
    actions = [event.get("focus_action") for event in sink.events]
    assert "NO_VIEW" in actions
    assert "MATCHED" in actions
    assert "LOOKUP_MISS" in actions


def test_throwing_gui_sink_does_not_change_lookup_or_display(qt_app):
    workspace = MarketWorkspace(warrior_observability=RecordingSink(fail=True))
    workspace.render(WatchlistSnapshot(rows=(_scanner(),)))
    workspace.render_warrior(_view(_warrior()))
    assert workspace.trade_intelligence._decision.text() == "WAIT"
    assert workspace.trade_intelligence._plan_values["Setup"].text() == "BULL_FLAG"


def test_composed_main_window_passes_the_same_sink_to_dashboard(tmp_path, monkeypatch, qt_app):
    monkeypatch.setenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_ENABLED", "true")
    monkeypatch.setenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_ROOT", str(tmp_path))
    monkeypatch.setenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_SESSION_ID", "composition-gui")
    composition = create_desktop_composition()
    window = MainWindow(
        composition.bus, composition.state_store, composition.runtime_service,
        composition.trading_service, composition.order_command_factory,
        warrior_forward_sidecar=composition.warrior_forward_sidecar,
        settings=QSettings(str(tmp_path / "layout.ini"), QSettings.Format.IniFormat),
    )
    try:
        sink = composition.warrior_forward_sidecar._observability
        assert window.dashboard.market_workspace._warrior_observability is sink
        assert isinstance(sink, BoundedJsonlWarriorObservabilitySink)
        assert len(list(tmp_path.glob("warrior-observability-*.jsonl"))) == 1
    finally:
        window.close()
        composition.close(timeout_seconds=1.0)


def test_composed_default_uses_noop_and_creates_no_diagnostic_file(tmp_path, monkeypatch, qt_app):
    monkeypatch.delenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_ENABLED", raising=False)
    monkeypatch.delenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_ROOT", raising=False)
    monkeypatch.delenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_SESSION_ID", raising=False)
    monkeypatch.setenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_ROOT", str(tmp_path))
    composition = create_desktop_composition()
    try:
        assert composition.warrior_forward_sidecar._observability is NOOP_WARRIOR_OBSERVABILITY
        assert not list(tmp_path.iterdir())
    finally:
        composition.close(timeout_seconds=1.0)

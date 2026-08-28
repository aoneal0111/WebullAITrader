from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.models import WatchlistRow, WatchlistSnapshot
from app.gui.widgets.market_workspace import MarketWorkspace


def _scanner_row(
    symbol: str, *, selected: bool = False, rank: str = "1", **overrides,
) -> WatchlistRow:
    values = dict(
        symbol=symbol, selected=selected, latest_price="2.08", change="+0.93",
        change_percent="+80.90%", bid="2.07", ask="2.08", volume="83,800,000",
        market_status="OPEN", last_update="now", stale="LIVE", rank=rank,
        score="94.00", relative_volume="48.80x", dollar_volume="$174,304,000",
        spread="0.48%", catalyst="SEC Filing", freshness="LIVE",
        session="AFTER_HOURS", classification="QUALIFYING", float_shares="7.84M",
    )
    values.update(overrides)
    return WatchlistRow(**values)


def _warrior_row(
    symbol: str,
    *,
    status: str,
    setup: str,
    setup_state: str,
    blockers: str = "--",
    **overrides,
) -> WatchlistRow:
    values = dict(
        symbol=symbol, selected=False, latest_price="2.08", change="--",
        change_percent="+80.90%", bid="--", ask="--", volume="83,800,000",
        market_status="AFTER_HOURS", last_update="now", stale="LIVE", rank="1",
        score="76.55", session="AFTER_HOURS", setup=setup,
        setup_state=setup_state, strategy_status=status,
        entry_trigger="2.1000" if setup != "NO SETUP" else "--",
        stop_price="1.9800" if setup != "NO SETUP" else "--",
        blocking_reasons=blockers, warrior_evaluated=True,
        warrior_score="76.55", warrior_status=status,
        warrior_session="AFTER_HOURS", strategy_name="Warrior Momentum",
    )
    values.update(overrides)
    return WatchlistRow(**values)


def _view(*rows: WatchlistRow):
    return SimpleNamespace(
        focus=WatchlistSnapshot(rows=rows), summary="", funnel="", research="",
        enabled=True, health="RUNNING",
    )


def test_warrior_focus_uses_compact_selector_and_trade_intelligence() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    selected: list[str] = []
    workspace.operator_symbol_selected.connect(selected.append)
    workspace.render(WatchlistSnapshot(rows=(WatchlistRow(
        symbol="XYZ", selected=False, latest_price="10.20", change="+2.20",
        change_percent="+27.50%", bid="10.18", ask="10.22", volume="1,000,000",
        market_status="OPEN", last_update="09:45:00", stale="LIVE", rank="1",
        score="82.4", relative_volume="10.0x", dollar_volume="$10,200,000",
        spread="+0.39%", catalyst="TRUE", freshness="LIVE", session="REGULAR",
        float_shares="6.0M", setup="BULL FLAG", setup_state="FORMING",
        distance_to_hod="+0.70%", strategy_status="SETUP FORMING",
        explanations="Ranked #1 | Bull flag forming",
    ),)))
    assert workspace.watchlist._table.columnCount() == 9
    assert workspace.watchlist._table.item(0, 7).text() == "EVALUATING"
    assert workspace.trade_intelligence._plan_values["Setup"].text() == "--"
    workspace.watchlist._select_row(0, 0)
    assert selected == []


def test_focus_mode_switch_preserves_current_atlas_and_warrior_selection() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    atlas = WatchlistSnapshot(rows=(WatchlistRow(
        symbol="ATLS", selected=False, latest_price="5", change="1",
        change_percent="20%", bid="4.9", ask="5.1", volume="100",
        market_status="OPEN", last_update="now", stale="LIVE", rank="1",
    ),))
    warrior = type("View", (), {
        "focus": WatchlistSnapshot(rows=(WatchlistRow(
            symbol="WARR", selected=False, latest_price="8", change="2",
            change_percent="30%", bid="7.9", ask="8.1", volume="200",
            market_status="OPEN", last_update="now", stale="LIVE", rank="1",
            strategy_status="TRIGGERED", blocking_reasons="catalyst, session",
        ),)),
        "summary": "Today: 0 trades · N/A", "funnel": "D 1 → Trig 1",
        "research": "Triggered but blocked: 1",
    })()
    selected = []
    workspace.operator_symbol_selected.connect(selected.append)
    workspace.render(atlas)
    workspace.render_warrior(warrior)
    workspace.watchlist._mode_selector.setCurrentText("WARRIOR PAPER")
    assert workspace.watchlist._table.item(0, 1).text().endswith("WARR")
    assert workspace.watchlist._paper_summary.text().endswith("N/A")
    workspace.watchlist._select_row(0, 1)
    workspace.watchlist._mode_selector.setCurrentText("CURRENT ATLAS")
    assert workspace.watchlist._table.item(0, 1).text().endswith("ATLS")
    assert selected == []


def test_after_hours_no_setup_omits_obsolete_session_blocker() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(rows=(_scanner_row("YYGH", selected=True),)))
    workspace.render_warrior(_view(_warrior_row(
        "YYGH", status="INELIGIBLE FOR EXECUTION", setup="NO SETUP",
        setup_state="--", blockers="No Warrior setup detected",
    )))

    panel = workspace.trade_intelligence
    assert panel._header_metrics["Scanner status"].text() == "QUALIFYING"
    assert panel._header_metrics["Warrior momentum"].text() == "INELIGIBLE FOR EXECUTION"
    assert panel._header_metrics["Session"].text() == "AFTER_HOURS"
    assert panel._watching_values["Warrior session"].text() == "AFTER_HOURS"
    assert panel._plan_values["Setup"].text() == "NO SETUP"
    assert panel._decision.text() == "WAIT"
    assert panel._blocking.text() == "No Warrior setup detected"
    assert "session is not allowed" not in panel._blocking.text().lower()
    assert panel._autonomous_paper.text() == "WAITING FOR SETUP"


def test_no_setup_without_an_additional_failed_gate_waits() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(rows=(_scanner_row("WAIT", selected=True),)))
    workspace.render_warrior(_view(_warrior_row(
        "WAIT", status="INELIGIBLE FOR EXECUTION", setup="NO SETUP",
        setup_state="--", blockers="No Warrior setup detected",
    )))

    assert workspace.trade_intelligence._decision.text() == "WAIT"
    assert workspace.trade_intelligence._autonomous_paper.text() == "WAITING FOR SETUP"


def test_forming_setup_populates_plan_without_claiming_submission() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(rows=(_scanner_row("FORM", selected=True),)))
    workspace.render_warrior(_view(_warrior_row(
        "FORM", status="SETUP FORMING", setup="BULL FLAG", setup_state="FORMING",
    )))

    panel = workspace.trade_intelligence
    assert panel._plan_values["Strategy"].text() == "Warrior Momentum"
    assert panel._plan_values["Setup"].text() == "BULL FLAG"
    assert panel._plan_values["Setup state"].text() == "FORMING"
    assert panel._plan_values["Entry trigger"].text() == "2.1000"
    assert panel._plan_values["Stop"].text() == "1.9800"
    assert panel._decision.text() == "WAIT"
    assert panel._autonomous_paper.text() == "WAITING FOR SETUP"


def test_triggered_entry_ready_populates_plan_without_submitting_order() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(rows=(_scanner_row("READY", selected=True),)))
    workspace.render_warrior(_view(_warrior_row(
        "READY", status="ENTRY READY", setup="HIGH OF DAY BREAKOUT",
        setup_state="TRIGGERED",
    )))

    panel = workspace.trade_intelligence
    assert panel._plan_values["Setup state"].text() == "TRIGGERED"
    assert panel._plan_values["Entry trigger"].text() == "2.1000"
    assert panel._plan_values["Stop"].text() == "1.9800"
    assert panel._decision.text() == "ENTRY READY"
    assert panel._autonomous_paper.text() == "ENTRY READY"


def test_triggered_setup_waiting_for_execution_quote_is_not_entry_ready() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(rows=(_scanner_row("WAITQ", selected=True),)))
    workspace.render_warrior(_view(_warrior_row(
        "WAITQ", status="AWAITING EXECUTION DATA",
        setup="HIGH OF DAY BREAKOUT", setup_state="TRIGGERED",
        blockers="Waiting for fresh bid/ask",
    )))

    panel = workspace.trade_intelligence
    assert panel._decision.text() == "WAITING FOR FRESH QUOTE"
    assert panel._autonomous_paper.text() == "WAITING FOR FRESH QUOTE"
    assert panel._blocking.text() == "Waiting for fresh bid/ask"


def test_aehl_newer_scanner_quote_keeps_authoritative_decision_basis() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    decision_time = "2026-08-28T22:32:00.044000+00:00"
    current_time = "2026-08-28T22:32:22.206000+00:00"
    workspace.render(WatchlistSnapshot(rows=(_scanner_row(
        "AEHL", selected=True, latest_price="6.12", bid="6.13", ask="6.16",
        spread="+0.49%", market_timestamp=current_time,
    ),)))
    workspace.render_warrior(_view(_warrior_row(
        "AEHL", status="INELIGIBLE FOR EXECUTION",
        setup="HIGH OF DAY BREAKOUT", setup_state="TRIGGERED",
        blockers="Spread is too wide", decision_timestamp=decision_time,
        decision_last="6.17", decision_bid="6.05", decision_ask="6.17",
        decision_spread="1.96%",
    )))

    panel = workspace.trade_intelligence
    assert panel._market_values["Last"].text() == "$6.12"
    assert panel._market_values["Bid"].text() == "$6.13"
    assert panel._market_values["Ask"].text() == "$6.16"
    assert panel._market_values["Spread"].text() == "+0.49%"
    assert panel._decision.text() == "BLOCKED"
    assert panel._blocking.text() == "Spread is too wide"
    assert "Bid $6.05" in panel._decision_basis.text()
    assert "Ask $6.17" in panel._decision_basis.text()
    assert "Spread 1.96%" in panel._decision_basis.text()
    assert panel._decision_relation.text() == "CURRENT QUOTE NEWER THAN DECISION"


def test_same_version_replaces_prior_wide_basis_without_leakage() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    old_time = "2026-08-28T22:32:00.044000+00:00"
    new_time = "2026-08-28T22:32:23.743000+00:00"
    workspace.render(WatchlistSnapshot(rows=(_scanner_row(
        "AEHL", selected=True, latest_price="6.12", bid="6.13", ask="6.16",
        spread="+0.49%", market_timestamp=new_time,
    ),)))
    workspace.render_warrior(_view(_warrior_row(
        "AEHL", status="INELIGIBLE FOR EXECUTION",
        setup="HIGH OF DAY BREAKOUT", setup_state="TRIGGERED",
        blockers="Spread is too wide", decision_timestamp=old_time,
        decision_last="6.17", decision_bid="6.05", decision_ask="6.17",
        decision_spread="1.96%",
    )))
    workspace.render_warrior(_view(_warrior_row(
        "AEHL", status="ENTRY READY", setup="HIGH OF DAY BREAKOUT",
        setup_state="TRIGGERED", decision_timestamp=new_time,
        decision_last="6.12", decision_bid="6.13", decision_ask="6.16",
        decision_spread="0.49%",
    )))

    panel = workspace.trade_intelligence
    assert panel._decision.text() == "ENTRY READY"
    assert panel._blocking.text() == "--"
    assert "Bid $6.13" in panel._decision_basis.text()
    assert "Spread 0.49%" in panel._decision_basis.text()
    assert "1.96%" not in panel._decision_basis.text()
    assert panel._decision_relation.text() == "CURRENT QUOTE MATCHES DECISION"


def test_symbol_switch_clears_unmatched_decision_basis() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    timestamp = "2026-08-28T22:32:23.743000+00:00"
    workspace.render(WatchlistSnapshot(rows=(
        _scanner_row("AEHL", selected=True, market_timestamp=timestamp),
        _scanner_row("OTHER", rank="2", market_timestamp=timestamp),
    )))
    workspace.render_warrior(_view(_warrior_row(
        "AEHL", status="INELIGIBLE FOR EXECUTION",
        setup="HIGH OF DAY BREAKOUT", setup_state="TRIGGERED",
        blockers="Spread is too wide", decision_timestamp=timestamp,
        decision_last="6.17", decision_bid="6.05", decision_ask="6.17",
        decision_spread="1.96%",
    )))
    workspace._select_candidate("OTHER")

    panel = workspace.trade_intelligence
    assert panel._symbol.text() == "OTHER"
    assert panel._decision_basis.text() == "--"
    assert panel._decision_relation.text() == "--"
    assert panel._blocking.text() == "--"


def test_selected_scanner_symbol_uses_matching_warrior_item_not_first_ranked() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(rows=(
        _scanner_row("AAAA", rank="1"),
        _scanner_row("BBBB", selected=True, rank="2"),
    )))
    workspace.render_warrior(_view(
        _warrior_row("AAAA", status="ENTRY READY", setup="BULL FLAG", setup_state="TRIGGERED"),
        _warrior_row("BBBB", status="INELIGIBLE FOR EXECUTION", setup="NO SETUP", setup_state="--"),
    ))

    panel = workspace.trade_intelligence
    assert panel._symbol.text() == "BBBB"
    assert panel._header_metrics["Warrior momentum"].text() == "INELIGIBLE FOR EXECUTION"
    assert panel._plan_values["Setup"].text() == "NO SETUP"


def test_scanner_symbol_without_matching_warrior_item_is_evaluating() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot(rows=(_scanner_row("BBBB", selected=True),)))
    workspace.render_warrior(_view(
        _warrior_row("AAAA", status="ENTRY READY", setup="BULL FLAG", setup_state="TRIGGERED"),
    ))

    panel = workspace.trade_intelligence
    assert panel._symbol.text() == "BBBB"
    assert panel._header_metrics["Warrior momentum"].text() == "EVALUATING"
    assert panel._plan_values["Setup"].text() == "--"
    assert panel._plan_values["Entry trigger"].text() == "--"
    assert panel._decision.text() == "EVALUATING"


def test_empty_state_remains_explicit_and_readable() -> None:
    app = QApplication.instance() or QApplication([])
    del app
    workspace = MarketWorkspace()
    workspace.render(WatchlistSnapshot())

    assert workspace.trade_intelligence._symbol.text() == "--"
    assert workspace.trade_intelligence._decision.text() == "--"
    assert workspace.trade_intelligence._autonomous_paper.text() == "--"

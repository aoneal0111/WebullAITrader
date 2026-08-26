"""Manual workstation preview and geometry probe.

Run with QT_QPA_PLATFORM=offscreen to create deterministic review artifacts.
This helper is intentionally outside the collected test modules.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow, QStatusBar

from app.gui.design.theme import application_stylesheet
from app.gui.models import (
    ActivityEntry,
    ActivitySnapshot,
    PortfolioDashboardSnapshot,
    HealthDashboardSnapshot,
    RuntimeSnapshot,
    WatchlistRow,
    WatchlistSnapshot,
)
from app.gui.pages.dashboard import DashboardPage
from app.gui.widgets.global_status_bar import GlobalStatusBar


def reference_runtime() -> RuntimeSnapshot:
    """Truthful, intentionally unavailable test-only workstation state."""
    return replace(
        RuntimeSnapshot.initial(),
        broker_status="Unknown",
        market_feed_status="Unknown",
    )


def candidate() -> WatchlistRow:
    return WatchlistRow(
        symbol="PMI", selected=True, latest_price="4.72", change="0.73",
        change_percent="+18.3%", bid="4.70", ask="4.73", volume="3.8M",
        market_status="OPEN", last_update="10:42:31 ET", stale="LIVE",
        rank="1", score="91", relative_volume="8.4x", dollar_volume="$17.6M",
        spread="0.8%", catalyst="News", passed_rules="Momentum; RVOL; HOD proximity",
        failed_rules="Entry trigger", freshness="1.2s", session="PRE-MARKET",
        classification="QUALIFYING", float_shares="5.4M", setup="HOD BREAK",
        setup_state="ARMED", distance_to_hod="-2.1%", strategy_status="WAIT",
        explanations="High-momentum candidate with strong relative volume and a clean HOD-break setup.",
        entry_trigger="> $4.79", stop_price="$4.49",
        blocking_reasons="Entry trigger not reached",
    )


def populate(page: DashboardPage) -> None:
    watchlist = WatchlistSnapshot(
        rows=(candidate(),), candidate_count=1, scanner_status="Active",
    )
    page.market_workspace.render(watchlist)
    page.runtime_header.render_watchlist(watchlist)
    now = datetime.now(timezone.utc)
    page.market_workspace.activity_panel.render(ActivitySnapshot(entries=(
        ActivityEntry(now, "PMI remains below the entry trigger", "DECISIONS", related_symbol="PMI"),
        ActivityEntry(now, "PMI promoted to qualifying", "SCANNER", related_symbol="PMI"),
        ActivityEntry(now, "Market data snapshot refreshed", "MARKET_DATA", related_symbol="PMI"),
    )))
    page.market_workspace.portfolio_summary.render(PortfolioDashboardSnapshot(
        metrics=(("Equity", "$25,240"), ("Cash", "$20,520"),
                 ("Buying Power", "$45,760"), ("Open Positions", "1"),
                 ("Total P/L", "+$240"), ("Unrealized P/L", "+$180"),
                 ("Realized P/L", "+$60"), ("Current Drawdown", "0.4%"),
                 ("Win Rate", "67%")),
        highlights=(("Exposure", "$4,720"), ("Gross Exposure", "$4,720"),
                    ("Net Exposure", "$4,720"),
                    ("Winning / Losing Positions", "2 / 1")),
    ))
    page.runtime_header.render_portfolio(PortfolioDashboardSnapshot(
        metrics=(("Equity", "$25,240"), ("Buying Power", "$45,760")),
        highlights=(),
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--state", choices=("empty", "populated"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(application_stylesheet())
    window = QMainWindow()
    window.setWindowTitle("Atlas X — WebullAITrader")
    page = DashboardPage()
    window.setCentralWidget(page)
    global_status = GlobalStatusBar(version="0.1.0")
    global_status.render_health(HealthDashboardSnapshot.initial())
    global_status.runtime.set_status("Runtime Stopped", "danger")
    status = QStatusBar()
    status.addWidget(global_status, 1)
    window.setStatusBar(status)
    runtime = reference_runtime()
    page.runtime_header.render(runtime)
    page.runtime_header.render_health(HealthDashboardSnapshot.initial())
    page.workstation_footer.set_value("Mode", runtime.environment)
    page.workstation_footer.set_value("Version", "0.1.0")
    if args.state == "populated":
        populate(page)
    else:
        page.market_workspace.portfolio_summary.render(
            PortfolioDashboardSnapshot(
                metrics=(("Total P/L", "$0.00"),
                         ("Unrealized P/L", "$0.00"),
                         ("Realized P/L", "$0.00"),
                         ("Open Positions", "0"),
                         ("Gross Exposure", "$0.00"),
                         ("Winning / Losing Positions", "0 / 0")),
                highlights=(),
            )
        )
    window.resize(args.width, args.height)
    window.show()
    app.processEvents()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(args.output)):
        raise RuntimeError(f"Could not save {args.output}")
    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

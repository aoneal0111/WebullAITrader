"""Explicit read-only Atlas equity ledger viewer; no trading composition."""
import argparse
import json
from pathlib import Path

from app.paper_trading.ledger_view import PaperLedgerReader


def create_window(reader):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QMainWindow, QPushButton, QTabWidget, QVBoxLayout, QWidget
    from app.asset_modules.engine_catalog import EngineId
    from app.gui.design.theme import application_stylesheet
    from app.gui.formatters.orders import format_orders
    from app.gui.pages.strategy_orders import StrategyOrdersPage
    from app.gui.widgets.orders_panel import OrdersPanel

    class Viewer(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Atlas — Read-only PAPER ledger")
            self.resize(1200, 800)
            self.setStyleSheet(application_stylesheet())
            root, layout = QWidget(), QVBoxLayout()
            root.setLayout(layout)
            self.setCentralWidget(root)
            self.notice = QLabel()
            self.notice.setTextFormat(Qt.TextFormat.PlainText)
            self.notice.setWordWrap(True)
            layout.addWidget(self.notice)
            refresh = QPushButton("Refresh saved ledger")
            refresh.setObjectName("secondaryButton")
            refresh.clicked.connect(self.refresh)
            layout.addWidget(refresh)
            self.tabs = QTabWidget()
            self.all_orders = OrdersPanel()
            self.tabs.addTab(self.all_orders, "Equity · working / recent")
            self.pages = []
            for engine, title in ((EngineId.WARRIOR,"Warrior"),(EngineId.SCALPER,"Quick Scalper")):
                page = StrategyOrdersPage(engine)
                page.notice.setText("READ ONLY · Net fills from the saved current campaign. Broker inventory and runtime/feed health are unverified.")
                self.pages.append(page)
                self.tabs.addTab(page, title)
            layout.addWidget(self.tabs)
            self.refresh()

        def refresh(self):
            snapshot = reader.read()
            self.notice.setText(f"READ ONLY · {snapshot.status} · Read at {snapshot.read_at}\n"
                                f"Campaign: {snapshot.campaign or 'UNKNOWN'}\n{snapshot.notice}")
            self.all_orders.render(format_orders(snapshot.orders))
            title = "No saved orders" if snapshot.status == "AVAILABLE" else "Saved orders unavailable"
            self.all_orders._table.set_empty_state(title, snapshot.notice)
            for page in self.pages:
                page.render_orders(snapshot.orders)
                page.orders_panel._table.set_empty_state(title, snapshot.notice)

    return Viewer()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Print a read-only summary without Qt")
    args = parser.parse_args(argv)
    reader = PaperLedgerReader(args.ledger)
    if args.check:
        report = reader.read().report()
        print(json.dumps(report, indent=2))
        return 0 if report["status"] == "AVAILABLE" else 1
    import sys
    from PySide6.QtWidgets import QApplication
    application = QApplication([sys.argv[0]])
    application.setApplicationName("Atlas Read-only PAPER Viewer")
    window = create_window(reader)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())

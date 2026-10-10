"""Read-only strategy ledger; does not infer ownership of aggregate positions."""
from collections import defaultdict
from decimal import Decimal

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem

from app.asset_modules.engine_catalog import ENGINE_READINESS, lifecycle_owner
from app.gui.formatters.orders import format_orders
from app.gui.widgets.orders_panel import MissionControlOrdersPanel
from app.read_models.orders import OrdersReadModelSnapshot, project_orders_read_model


class StrategyOrdersPage(QWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Warrior" if engine.value.startswith("WARRIOR") else "Quick Scalper"))
        self.notice = QLabel(
            ENGINE_READINESS[engine] + ".\n"
            "Inventory below is net fills in the current order snapshot, not reconciled broker positions. "
            "Account positions and protection remain under Equity."
        )
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.inventory = QTableWidget(0, 3)
        self.inventory.setHorizontalHeaderLabels(["Symbol", "Lifecycle", "Net filled shares"])
        self.inventory.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.inventory)
        self.orders_panel = MissionControlOrdersPanel()
        layout.addWidget(self.orders_panel)

    def render_state(self, state):
        orders = tuple(o for o in project_orders_read_model(state).orders
                       if lifecycle_owner(o.lifecycle_id) is self.engine)
        self.orders_panel.render(format_orders(OrdersReadModelSnapshot(orders)))
        net = defaultdict(Decimal)
        for order in orders:
            if order.side.upper() in {"BUY", "SELL"}:
                net[(order.symbol, order.lifecycle_id)] += Decimal(order.filled_quantity or "0") * (
                    1 if order.side.upper() == "BUY" else -1
                )
        rows = sorted((symbol, lifecycle, str(qty)) for (symbol, lifecycle), qty in net.items() if qty)
        self.inventory.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(row):
                self.inventory.setItem(index, column, QTableWidgetItem(value))

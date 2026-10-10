from datetime import datetime, timezone

from PySide6.QtWidgets import QApplication

from app.asset_modules.engine_catalog import EngineId, lifecycle_owner
from app.gui.pages.strategy_orders import StrategyOrdersPage
from app.gui.widgets.asset_navigation import AssetNavigation
from app.operations_core import ApplicationState, OperationsOrder


def test_exact_owner_prefix_required():
    assert lifecycle_owner("QUICK_SCALPER|SPY|one") is EngineId.SCALPER
    assert lifecycle_owner("WARRIOR_MOMENTUM_V1|SPY|two") is EngineId.WARRIOR
    for value in (None, "QUICK_SCALPER", "QUICK_SCALPER|", "OTHER|QUICK_SCALPER|SPY"):
        assert lifecycle_owner(value) is None


def test_strategy_views_keep_same_symbol_lifecycles_separate():
    app = QApplication.instance() or QApplication([])
    now = datetime.now(timezone.utc)
    def order(number, lifecycle, side, qty, status="FILLED"):
        return OperationsOrder(order_id=str(number), symbol="SPY", side=side,
                               quantity=str(qty), status=status, updated_at=now,
                               lifecycle_id=lifecycle, filled_quantity=str(qty))
    state = ApplicationState(orders=(
        order(1, "QUICK_SCALPER|SPY|one", "BUY", 10),
        order(2, "QUICK_SCALPER|SPY|one", "SELL", 3, "CANCELLED"),
        order(3, "WARRIOR_MOMENTUM_V1|SPY|two", "BUY", 20),
        order(4, None, "BUY", 50),
    ))
    page = StrategyOrdersPage(EngineId.SCALPER)
    page.render_state(state)
    assert page.inventory.rowCount() == 1
    assert page.inventory.item(0, 2).text() == "7"
    assert page.orders_panel._table.rowCount() == 2
    page.render_state(ApplicationState())
    assert page.inventory.rowCount() == 0
    page.close()


def test_navigation_does_not_start_engine_and_preserves_market_indices():
    app = QApplication.instance() or QApplication([])
    nav = AssetNavigation()
    selected = []
    nav.asset_selected.connect(selected.append)
    nav.tabs.setCurrentIndex(5)
    assert selected == [EngineId.SCALPER]
    assert nav.tabs.tabText(1) == "Crypto"
    assert nav.toggle.isHidden()
    assert "EQUITY STRATEGY VIEW" in nav.status.text()
    nav.close()

from datetime import UTC, datetime

from app.gui.presenters import OrdersPresenter
from app.operations_core import ApplicationState, OperationsOrder
from app.read_models.orders.projector import project_operational_orders


NOW = datetime(2026, 7, 30, 14, 0, tzinfo=UTC)


class OrdersPageSpy:
    def __init__(self) -> None:
        self.states = []
        self.projections = []

    def render(self, state) -> None:
        self.states.append(state)

    def render_projection(self, projection) -> None:
        self.projections.append(projection)


def test_orders_presenter_delegates_immutable_projection() -> None:
    page = OrdersPageSpy()
    presenter = OrdersPresenter(page)  # type: ignore[arg-type]
    projection = project_operational_orders(
        (
            OperationsOrder(
                order_id="order-1",
                symbol="AAPL",
                side="BUY",
                quantity="10",
                status="FILLED",
                updated_at=NOW,
            ),
        )
    )
    state = ApplicationState(order_projection=projection)

    presenter.render(state)

    assert page.states == [state]
    assert page.projections == [projection]


def test_orders_presenter_preserves_existing_page_data_for_empty_projection() -> None:
    page = OrdersPageSpy()
    state = ApplicationState()

    OrdersPresenter(page).render(state)  # type: ignore[arg-type]

    assert page.states == [state]
    assert page.projections == []

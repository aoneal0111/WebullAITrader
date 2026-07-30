from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QWidget

from app.gui.pages.dashboard import DashboardPage
from app.gui.pages.orders import OrdersPage
from app.gui.formatters import format_positions
from app.gui.formatters import format_decisions
from app.gui.formatters import format_portfolio
from app.gui.projections.dashboard_projection import project_dashboard
from app.gui.projections.activity_projection import project_timeline_activity
from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.positions_panel import PositionsPanel
from app.operations_core import ApplicationState, RuntimePhase
from app.read_models.positions import project_positions_read_model


class ApplicationStatePresenter(Protocol):
    """Render one concern from an immutable application-state snapshot."""

    def render(self, state: ApplicationState) -> None:
        """Render the presenter's concern from the supplied state."""


class PresentationCoordinator:
    """Fan one application-state update out to focused presenters."""

    def __init__(self, presenters: Sequence[ApplicationStatePresenter]) -> None:
        self._presenters = tuple(presenters)

    def render(self, state: ApplicationState) -> None:
        for presenter in self._presenters:
            presenter.render(state)


class DashboardPresenter:
    """Project application state into the dashboard's immutable view model."""

    def __init__(self, dashboard: DashboardPage) -> None:
        self._dashboard = dashboard

    def render(self, state: ApplicationState) -> None:
        self._dashboard.render(project_dashboard(state))


class OrdersPresenter:
    """Render the event-driven immutable orders projection."""

    def __init__(self, orders_page: OrdersPage) -> None:
        self._orders_page = orders_page

    def render(self, state: ApplicationState) -> None:
        self._orders_page.render(state)
        if state.order_projection.orders:
            self._orders_page.render_projection(state.order_projection)


class PositionsPresenter:
    """Prepare and render the immutable positions view model."""

    def __init__(self, positions_panel: PositionsPanel) -> None:
        self._positions_panel = positions_panel

    def render(self, state: ApplicationState) -> None:
        read_model = project_positions_read_model(state)
        self._positions_panel.render(format_positions(read_model))


class TimelinePresenter:
    """Prepare and render the immutable timeline activity view model."""

    def __init__(self, activity_panel: ActivityPanel) -> None:
        self._activity_panel = activity_panel

    def render(self, state: ApplicationState) -> None:
        self._activity_panel.render(project_timeline_activity(state))


class DecisionsPresenter:
    """Prepare the immutable decision lifecycle view model."""

    def __init__(self, decisions_view) -> None:
        self._decisions_view = decisions_view

    def render(self, state: ApplicationState) -> None:
        self._decisions_view.render(
            format_decisions(state.decision_projection)
        )


class PortfolioPresenter:
    """Prepare the immutable aggregate portfolio dashboard model."""

    def __init__(self, portfolio_view) -> None:
        self._portfolio_view = portfolio_view

    def render(self, state: ApplicationState) -> None:
        self._portfolio_view.render(
            format_portfolio(state.portfolio_projection)
        )


class RuntimeControlsPresenter:
    """Keep runtime command availability synchronized with runtime phase."""

    def __init__(self, start_button: QPushButton, stop_button: QPushButton) -> None:
        self._start_button = start_button
        self._stop_button = stop_button

    def render(self, state: ApplicationState) -> None:
        phase = state.runtime.phase
        active = phase in {
            RuntimePhase.STARTING,
            RuntimePhase.RUNNING,
            RuntimePhase.STOPPING,
        }
        self._start_button.setEnabled(not active)
        self._stop_button.setEnabled(
            phase in {RuntimePhase.STARTING, RuntimePhase.RUNNING}
        )


class RuntimeStatusPresenter:
    """Render the concise runtime summary shown in the status bar."""

    def __init__(self, status_label: QLabel) -> None:
        self._status_label = status_label

    def render(self, state: ApplicationState) -> None:
        runtime = state.runtime
        self._status_label.setText(
            f"{runtime.environment}  |  Runtime {runtime.phase.value}  |  "
            f"Feed {runtime.market_feed_status}  |  Cycles {runtime.cycles_completed}"
        )


class RuntimeErrorPresenter:
    """Display each distinct runtime failure once."""

    def __init__(
        self,
        parent: QWidget,
        show_error: Callable[[QWidget, str, str], object] = QMessageBox.critical,
    ) -> None:
        self._parent = parent
        self._show_error = show_error
        self._last_error = ""

    def render(self, state: ApplicationState) -> None:
        runtime = state.runtime
        error = runtime.last_error
        if (
            runtime.phase is RuntimePhase.FAILED
            and error
            and error != self._last_error
        ):
            self._last_error = error
            self._show_error(self._parent, "Runtime Error", error)

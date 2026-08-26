from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QWidget

from app.gui.pages.dashboard import DashboardPage
from app.gui.pages.orders import OrdersPage
from app.gui.formatters import format_positions
from app.gui.formatters import format_decisions
from app.gui.formatters import format_portfolio
from app.gui.formatters import format_health
from app.gui.formatters import format_sorted_watchlist
from app.gui.formatters import format_replay
from app.gui.formatters import format_timeline
from app.gui.models import TimelineFilter
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

    def __init__(self, dashboard: DashboardPage, *additional_views) -> None:
        self._views = (dashboard, *additional_views)

    def render(self, state: ApplicationState) -> None:
        snapshot = project_dashboard(state)
        for view in self._views:
            view.render(snapshot)


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

    _LIVE_ACTIVITY_LIMIT = 100

    def __init__(self, activity_panel: ActivityPanel, *additional_views) -> None:
        self._activity_views = (activity_panel, *additional_views)
        self._filters = TimelineFilter()
        self._state: ApplicationState | None = None

    def render(self, state: ApplicationState) -> None:
        self._state = state
        snapshot = format_timeline(
            project_timeline_activity(state, limit=self._LIVE_ACTIVITY_LIMIT),
            self._filters,
        )
        for view in self._activity_views:
            view.render(snapshot)

    def set_filters(self, filters: TimelineFilter) -> None:
        if not isinstance(filters, TimelineFilter):
            raise TypeError("filters must be a TimelineFilter")
        self._filters = filters
        if self._state is not None:
            self.render(self._state)


class DecisionsPresenter:
    """Prepare the immutable decision lifecycle view model."""

    def __init__(self, decisions_view, *additional_views) -> None:
        self._decision_views = (decisions_view, *additional_views)
        self._selected_decision_id: str | None = None
        self._state: ApplicationState | None = None

    def render(self, state: ApplicationState) -> None:
        self._state = state
        snapshot = format_decisions(
            state.decision_projection,
            selected_decision_id=self._selected_decision_id,
        )
        for view in self._decision_views:
            view.render(snapshot)

    def select_decision(self, decision_id: str) -> None:
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError("decision_id must be non-empty text")
        self._selected_decision_id = decision_id
        if self._state is not None:
            self.render(self._state)


class PortfolioPresenter:
    """Prepare the immutable aggregate portfolio dashboard model."""

    def __init__(self, portfolio_view, *additional_views) -> None:
        self._portfolio_views = (portfolio_view, *additional_views)

    def render(self, state: ApplicationState) -> None:
        account = state.broker_account
        snapshot = format_portfolio(
            state.portfolio_projection,
            equity=(
                account.equity
                if account is not None
                else state.paper_runtime.current_equity
                if state.paper_runtime is not None
                else None
            ),
            buying_power=(
                account.buying_power
                if account is not None
                else None
            ),
            cash=(
                account.cash_balance
                if account is not None
                else None
            ),
            intelligence=state.portfolio_intelligence,
            current_drawdown=(
                state.paper_runtime.current_drawdown
                if state.paper_runtime is not None
                else None
            ),
        )
        for view in self._portfolio_views:
            view.render(snapshot)


class HealthPresenter:
    """Prepare the immutable infrastructure health dashboard model."""

    def __init__(self, health_view, *additional_views) -> None:
        self._health_views = (health_view, *additional_views)

    def render(self, state: ApplicationState) -> None:
        snapshot = format_health(state.health_projection)
        for view in self._health_views:
            view.render(snapshot)


class WatchlistPresenter:
    """Prepare the immutable event-driven watchlist UI model."""

    def __init__(self, watchlist_view, *additional_views) -> None:
        self._watchlist_views = (watchlist_view, *additional_views)
        self._sort_field = "projection"
        self._descending = False
        self._state: ApplicationState | None = None

    def render(self, state: ApplicationState) -> None:
        self._state = state
        snapshot = format_sorted_watchlist(
            state.watchlist_projection,
            sort_field=self._sort_field,
            descending=self._descending,
            health=state.health_projection,
        )
        for view in self._watchlist_views:
            view.render(snapshot)

    def sort_by(self, field_name: str) -> None:
        if field_name == self._sort_field:
            self._descending = not self._descending
        else:
            self._sort_field = field_name
            self._descending = False
        if self._state is not None:
            self.render(self._state)


class ReplayPresenter:
    """Prepare the immutable operator replay workspace model."""

    def __init__(self, replay_view, *additional_views) -> None:
        self._replay_views = (replay_view, *additional_views)

    def render(self, state: ApplicationState) -> None:
        snapshot = format_replay(state.replay)
        for view in self._replay_views:
            view.render(snapshot)


class RuntimeControlsPresenter:
    """Keep runtime command availability synchronized with runtime phase."""

    def __init__(self, start_button: QPushButton, stop_button: QPushButton, status_view=None) -> None:
        self._start_button = start_button
        self._stop_button = stop_button
        self._status_view = status_view

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
        if self._status_view is not None:
            self._status_view.set_runtime_status(
                state.runtime.environment,
                phase.value,
            )


class RuntimeStatusPresenter:
    """Render the concise runtime summary shown in the status bar."""

    def __init__(self, status_label: QLabel) -> None:
        self._status_label = status_label

    def render(self, state: ApplicationState) -> None:
        runtime = state.runtime
        health = state.health_projection
        market_feed_status = (
            health.market_data_status or runtime.market_feed_status
        )
        self._status_label.setText(
            f"{runtime.environment}  |  Runtime {runtime.phase.value}  |  "
            f"Feed {market_feed_status}  |  Cycles {runtime.cycles_completed}"
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

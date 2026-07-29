from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.gui.components import (
    InfrastructureCards,
    PanelHeader,
    PortfolioCards,
    RuntimeRibbon,
)
from app.gui.components.layout import WorkstationSplitter
from app.gui.models import (
    CandleInterval,
    CandleSeriesSnapshot,
    DashboardSnapshot,
)
from app.gui.theme.spacing import Spacing
from app.gui.widgets.analytics_panel import AnalyticsPanel
from app.gui.widgets.candlestick_chart import CandlestickChart
from app.gui.widgets.decision_center import DecisionCenter
from app.gui.widgets.event_store_panel import EventStorePanel
from app.gui.widgets.experiment_panel import ExperimentPanel
from app.gui.widgets.orders_panel import OrdersPanel
from app.gui.widgets.panel import SectionPanel
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.replay_panel import ReplayPanel
from app.gui.widgets.runtime_health_panel import RuntimeHealthPanel
from app.gui.widgets.timeline_panel import TimelinePanel
from app.gui.widgets.trade_lifecycle_panel import TradeLifecyclePanel
from app.operations_core import (
    OperatorDecisionSelected,
    OperatorSymbolSelected,
    OperatorTimelineSelected,
    OperatorTradeSelected,
)


class DashboardPage(QWidget):
    """Responsive Atlas workstation composed entirely from read-only widgets."""

    selection_requested = Signal(object)
    start_runtime_requested = Signal()
    stop_runtime_requested = Signal()
    runtime_control_requested = Signal()
    replay_play_requested = Signal()
    replay_pause_requested = Signal()
    replay_stop_requested = Signal()
    replay_step_forward_requested = Signal()
    replay_step_backward_requested = Signal()
    replay_jump_requested = Signal(int)
    replay_speed_requested = Signal(object)
    recording_open_requested = Signal()
    recording_save_requested = Signal()
    event_store_query_requested = Signal(str, object)
    event_store_replay_requested = Signal(str)
    event_store_refresh_requested = Signal()
    experiment_start_requested = Signal(str, str, str)
    experiment_pause_requested = Signal()
    experiment_resume_requested = Signal()
    experiment_step_requested = Signal()
    experiment_stop_requested = Signal()
    experiment_compare_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("workspaceSurface")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self.runtime_ribbon = RuntimeRibbon()
        self.runtime_ribbon.start_requested.connect(
            self.start_runtime_requested
        )
        self.runtime_ribbon.stop_requested.connect(
            self.stop_runtime_requested
        )
        root.addWidget(self.runtime_ribbon)

        root.addWidget(PanelHeader("Infrastructure"))
        self.infrastructure_cards = InfrastructureCards()
        root.addWidget(self.infrastructure_cards)

        root.addWidget(PanelHeader("Portfolio"))
        self.portfolio_metrics = PortfolioCards()
        root.addWidget(self.portfolio_metrics)

        self.chart = CandlestickChart()
        self.chart_panel = SectionPanel(
            "Market Chart",
            self.chart,
        )
        self.chart_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.operator_workspace = QFrame()
        self.operator_workspace.setObjectName("operatorWorkspace")
        self.operator_workspace.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        operator_layout = QVBoxLayout(self.operator_workspace)
        operator_layout.setContentsMargins(10, 10, 10, 10)
        operator_layout.setSpacing(Spacing.SM)
        operator_layout.addWidget(PanelHeader("Operator Workspace"))
        self.workspace_tabs = QTabWidget()
        self.workspace_tabs.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Expanding,
        )
        operator_layout.addWidget(self.workspace_tabs, 1)

        self.timeline_panel = TimelinePanel()
        self.decision_center = DecisionCenter()
        self.positions_panel = PositionsPanel()
        self.orders_panel = OrdersPanel()
        self.trade_lifecycle_panel = TradeLifecyclePanel()
        self.runtime_health_panel = RuntimeHealthPanel()
        self.workspace_tabs.addTab(self.positions_panel, "Positions")
        self.workspace_tabs.addTab(self.orders_panel, "Orders")
        self.workspace_tabs.addTab(self.decision_center, "Decisions")
        self.workspace_tabs.addTab(self.timeline_panel, "Timeline")
        self.workspace_tabs.addTab(
            self.trade_lifecycle_panel,
            "Lifecycle",
        )
        self.workspace_tabs.addTab(
            self.runtime_health_panel,
            "Health",
        )

        self.timeline_panel.selection_requested.connect(
            self._select_timeline
        )
        self.decision_center.selection_requested.connect(
            self._select_decision
        )
        self.trade_lifecycle_panel.selection_requested.connect(
            self._select_trade
        )
        self.positions_panel.selection_requested.connect(
            self._select_position
        )
        self.orders_panel.selection_requested.connect(
            self._select_order
        )

        self.workspace_splitter = WorkstationSplitter(
            self.chart_panel,
            self.operator_workspace,
        )
        root.addWidget(self.workspace_splitter, 1)

        # These established tools remain constructed and wired for their
        # dedicated navigation pages. They stay outside the primary
        # workstation so the chart remains the visual centerpiece.
        self._support_surfaces = QWidget(self)
        self._support_surfaces.hide()
        self.replay_panel = ReplayPanel()
        self.event_store_panel = EventStorePanel()
        self.analytics_panel = AnalyticsPanel()
        self.experiment_panel = ExperimentPanel()
        for panel in (
            self.replay_panel,
            self.event_store_panel,
            self.analytics_panel,
            self.experiment_panel,
        ):
            panel.setParent(self._support_surfaces)
        self._connect_support_surfaces()

        # Compatibility with the former dashboard public attributes.
        self.mode_badge = self.runtime_ribbon.mode.value_label

    def _connect_support_surfaces(self) -> None:
        self.replay_panel.play_requested.connect(
            self.replay_play_requested
        )
        self.replay_panel.pause_requested.connect(
            self.replay_pause_requested
        )
        self.replay_panel.stop_requested.connect(
            self.replay_stop_requested
        )
        self.replay_panel.step_forward_requested.connect(
            self.replay_step_forward_requested
        )
        self.replay_panel.step_backward_requested.connect(
            self.replay_step_backward_requested
        )
        self.replay_panel.jump_requested.connect(
            self.replay_jump_requested
        )
        self.replay_panel.speed_requested.connect(
            self.replay_speed_requested
        )
        self.replay_panel.open_recording_requested.connect(
            self.recording_open_requested
        )
        self.replay_panel.save_recording_requested.connect(
            self.recording_save_requested
        )
        self.event_store_panel.search_requested.connect(
            lambda value: self.event_store_query_requested.emit(
                "search",
                value,
            )
        )
        self.event_store_panel.session_requested.connect(
            lambda value: self.event_store_query_requested.emit(
                "session",
                value,
            )
        )
        self.event_store_panel.symbol_requested.connect(
            lambda value: self.event_store_query_requested.emit(
                "all" if not value else "symbol",
                value,
            )
        )
        self.event_store_panel.event_type_requested.connect(
            lambda value: self.event_store_query_requested.emit(
                "all" if not value else "event_type",
                value,
            )
        )
        self.event_store_panel.replay_requested.connect(
            self.event_store_replay_requested
        )
        self.event_store_panel.refresh_requested.connect(
            self.event_store_refresh_requested
        )
        self.experiment_panel.start_requested.connect(
            self.experiment_start_requested
        )
        self.experiment_panel.pause_requested.connect(
            self.experiment_pause_requested
        )
        self.experiment_panel.resume_requested.connect(
            self.experiment_resume_requested
        )
        self.experiment_panel.step_requested.connect(
            self.experiment_step_requested
        )
        self.experiment_panel.stop_requested.connect(
            self.experiment_stop_requested
        )
        self.experiment_panel.compare_requested.connect(
            self.experiment_compare_requested
        )

    def render(self, snapshot: DashboardSnapshot) -> None:
        if not isinstance(snapshot, DashboardSnapshot):
            raise TypeError("snapshot must be a DashboardSnapshot")
        self.runtime_ribbon.render(snapshot.runtime)
        self.infrastructure_cards.render(
            snapshot.runtime,
            snapshot.runtime_health,
            snapshot.recording,
            snapshot.event_store,
        )
        self.portfolio_metrics.render(snapshot.portfolio)
        self.chart.render(
            CandleSeriesSnapshot(
                snapshot.operator_workspace.selected_symbol,
                CandleInterval.ONE_MINUTE,
            )
        )
        self.runtime_health_panel.render(snapshot.runtime_health)
        self.replay_panel.render(
            snapshot.replay,
            snapshot.recording,
        )
        self.event_store_panel.render(snapshot.event_store)
        self.analytics_panel.render(snapshot.analytics)
        self.experiment_panel.render(snapshot.experiments)
        self.timeline_panel.render(snapshot.timeline)
        self.decision_center.render(snapshot.decisions)
        self.positions_panel.render(snapshot.positions)
        self.orders_panel.render(snapshot.orders)
        self.trade_lifecycle_panel.render(
            snapshot.lifecycle_explorer
        )

    def _select_timeline(
        self,
        symbol: str,
        timeline_entry_id: str,
    ) -> None:
        self.selection_requested.emit(
            OperatorTimelineSelected(
                timeline_entry_id=timeline_entry_id,
                symbol=symbol or None,
                source="atlas-timeline",
            )
        )

    def _select_decision(
        self,
        symbol: str,
        decision_id: str,
    ) -> None:
        self.selection_requested.emit(
            OperatorDecisionSelected(
                symbol=symbol,
                decision_id=decision_id,
                source="atlas-decision",
            )
        )

    def _select_trade(self, symbol: str) -> None:
        self.selection_requested.emit(
            OperatorTradeSelected(
                symbol=symbol,
                source="atlas-trade",
            )
        )

    def _select_position(self, symbol: str) -> None:
        self.selection_requested.emit(
            OperatorSymbolSelected(
                symbol=symbol,
                selection_source="POSITION",
                source="atlas-position",
            )
        )

    def _select_order(self, symbol: str, order_id: str) -> None:
        self.selection_requested.emit(
            OperatorSymbolSelected(
                symbol=symbol,
                selection_source="ORDER",
                selection_id=order_id,
                source="atlas-order",
            )
        )

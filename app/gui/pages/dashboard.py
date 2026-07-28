from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.models import CandleInterval, CandleSeriesSnapshot, DashboardSnapshot
from app.operations_core import (
    OperatorDecisionSelected,
    OperatorSymbolSelected,
    OperatorTimelineSelected,
    OperatorTradeSelected,
)
from app.gui.widgets.common import StatusBadge
from app.gui.widgets.decision_center import DecisionCenter
from app.gui.widgets.analytics_panel import AnalyticsPanel
from app.gui.widgets.experiment_panel import ExperimentPanel
from app.gui.widgets.event_store_panel import EventStorePanel
from app.gui.widgets.orders_panel import OrdersPanel
from app.gui.widgets.panel import SectionPanel
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.portfolio_metrics import PortfolioMetrics
from app.gui.widgets.replay_panel import ReplayPanel
from app.gui.widgets.runtime_ribbon import RuntimeRibbon
from app.gui.widgets.runtime_health_panel import RuntimeHealthPanel
from app.gui.widgets.timeline_panel import TimelinePanel
from app.gui.widgets.trade_lifecycle_panel import TradeLifecyclePanel
from app.gui.widgets.candlestick_chart import CandlestickChart


class DashboardPage(QWidget):
    selection_requested = Signal(object)
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

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        header = QHBoxLayout()
        heading = QVBoxLayout()

        title = QLabel("Operator Console")
        title.setObjectName("pageTitle")

        subtitle = QLabel(
            "Live paper-trading operations at a glance."
        )
        subtitle.setObjectName("muted")

        heading.addWidget(title)
        heading.addWidget(subtitle)

        self.mode_badge = StatusBadge("PAPER")

        header.addLayout(heading)
        header.addStretch()
        self.runtime_button = QToolButton()
        self.runtime_button.setText("Start Paper Runtime")
        self.runtime_button.setObjectName("primaryButton")
        self.runtime_button.clicked.connect(self.runtime_control_requested)
        header.addWidget(self.runtime_button)
        header.addWidget(self.mode_badge)

        root.addLayout(header)

        self.runtime_ribbon = RuntimeRibbon()
        root.addWidget(self.runtime_ribbon)

        self.chart = CandlestickChart()
        chart_panel = SectionPanel("LIVE MARKET CHART", self.chart)
        root.addWidget(chart_panel, 1)

        self.replay_panel = ReplayPanel()
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
        replay_section = SectionPanel(
                "Session Replay",
                self.replay_panel,
            )
        replay_section.setVisible(False)
        root.addWidget(replay_section)

        self.event_store_panel = EventStorePanel()
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
        event_store_section = SectionPanel(
                "Historical Event Store - Read Only",
                self.event_store_panel,
            )
        event_store_section.setVisible(False)
        root.addWidget(event_store_section)

        self.analytics_panel = AnalyticsPanel()
        analytics_section = SectionPanel(
                "Historical Analytics - Read Only",
                self.analytics_panel,
            )
        analytics_section.setVisible(False)
        root.addWidget(analytics_section)

        self.experiment_panel = ExperimentPanel()
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
        experiment_section = SectionPanel(
                "Historical Experiment Center",
                self.experiment_panel,
            )
        experiment_section.setVisible(False)
        root.addWidget(experiment_section)

        self.portfolio_metrics = PortfolioMetrics()
        root.addWidget(self.portfolio_metrics)

        self.runtime_health_panel = RuntimeHealthPanel()
        health_section = SectionPanel(
                "Runtime Health Center - Read Only",
                self.runtime_health_panel,
            )
        root.addWidget(health_section)

        body = QGridLayout()
        body.setSpacing(12)

        self.timeline_panel = TimelinePanel()
        self.decision_center = DecisionCenter()
        self.positions_panel = PositionsPanel()
        self.orders_panel = OrdersPanel()
        self.trade_lifecycle_panel = TradeLifecyclePanel()
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

        decision_section = SectionPanel(
                "AI Decision Center - Read Only",
                self.decision_center,
            )
        decision_section.setVisible(False)
        body.addWidget(decision_section, 0, 0, 1, 2)

        timeline_section = SectionPanel(
                "Immutable Event Timeline - Read Only",
                self.timeline_panel,
            )
        timeline_section.setVisible(False)
        body.addWidget(timeline_section, 1, 0, 1, 2)

        positions_section = SectionPanel(
                "Open Positions",
                self.positions_panel,
            )
        body.addWidget(positions_section, 0, 2)

        orders_section = SectionPanel(
                "Active Orders",
                self.orders_panel,
            )
        body.addWidget(orders_section, 1, 2)

        lifecycle_section = SectionPanel(
                "Trade Lifecycle Explorer - Read Only",
                self.trade_lifecycle_panel,
            )
        lifecycle_section.setVisible(False)
        body.addWidget(lifecycle_section, 2, 0, 1, 3)

        body.setColumnStretch(0, 2)
        body.setColumnStretch(1, 2)
        body.setColumnStretch(2, 3)

        root.addLayout(body, 1)

    def render(self, snapshot: DashboardSnapshot) -> None:
        self.runtime_ribbon.render(snapshot.runtime)
        phase = snapshot.runtime.state.value
        transitioning = phase in {"STARTING", "STOPPING"}
        self.runtime_button.setEnabled(not transitioning)
        self.runtime_button.setText(
            "Stop Runtime" if phase in {"RUNNING", "STARTING"} else "Start Paper Runtime"
        )
        self.chart.render(
            CandleSeriesSnapshot(
                snapshot.operator_workspace.selected_symbol,
                CandleInterval.ONE_MINUTE,
            )
        )
        self.portfolio_metrics.render(snapshot.portfolio)
        self.runtime_health_panel.render(snapshot.runtime_health)
        self.replay_panel.render(
            snapshot.replay,
            snapshot.recording,
        )
        self.event_store_panel.render(snapshot.event_store)
        self.analytics_panel.render(snapshot.analytics)
        self.experiment_panel.render(snapshot.experiments)

        level = (
            "good"
            if snapshot.runtime.state.value == "RUNNING"
            else "warn"
        )

        self.mode_badge.set_status(
            snapshot.runtime.environment,
            level,
        )

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

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.gui.widgets.common import MetricCard, StatusBadge
from app.gui.widgets.panel import SectionPanel
from app.operations_core import ApplicationState, RuntimePhase


class EquityCurveWidget(QWidget):
    """Small dependency-free equity preview used until account history is connected."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(185)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._values: tuple[float, ...] = (100.0, 100.4, 100.1, 100.8, 101.2, 101.0, 101.7, 102.1, 101.9, 102.7)

    def set_values(self, values: Iterable[float]) -> None:
        normalized = tuple(float(value) for value in values)
        if len(normalized) >= 2:
            self._values = normalized
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(14, 14, -14, -18)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        grid_pen = QPen(QColor("#253041"))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        for row in range(1, 4):
            y = rect.top() + rect.height() * row / 4
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))

        minimum = min(self._values)
        maximum = max(self._values)
        spread = maximum - minimum or 1.0
        points: list[QPointF] = []
        for index, value in enumerate(self._values):
            x = rect.left() + rect.width() * index / (len(self._values) - 1)
            y = rect.bottom() - rect.height() * (value - minimum) / spread
            points.append(QPointF(x, y))

        path = QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)

        line_pen = QPen(QColor("#4f8cff"))
        line_pen.setWidth(2)
        painter.setPen(line_pen)
        painter.drawPath(path)

        painter.setPen(QColor("#8fa1b8"))
        painter.drawText(rect.left(), self.height() - 4, "SESSION EQUITY PREVIEW")


class RuntimeSummary(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("runtimeSummary")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        heading = QLabel("Runtime health")
        heading.setObjectName("panelTitle")
        layout.addWidget(heading)

        self.phase = self._row(layout, "Lifecycle", "Stopped")
        self.broker = self._row(layout, "Broker", "Disconnected")
        self.feed = self._row(layout, "Market feed", "Idle")
        self.inference = self._row(layout, "Inference", "Not loaded")
        layout.addStretch()

    @staticmethod
    def _row(layout: QVBoxLayout, title: str, value: str) -> QLabel:
        row = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("muted")
        output = QLabel(value)
        output.setObjectName("runtimeValue")
        output.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label)
        row.addStretch()
        row.addWidget(output)
        layout.addLayout(row)
        return output


class DashboardPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(3)
        title = QLabel("Trading Command Center")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Paper runtime oversight, portfolio readiness, and autonomous decision activity.")
        subtitle.setObjectName("muted")
        heading.addWidget(title)
        heading.addWidget(subtitle)
        self.mode_badge = StatusBadge("PAPER")
        header.addLayout(heading)
        header.addStretch()
        header.addWidget(self.mode_badge)
        root.addLayout(header)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        self.account_value = MetricCard("Account Value", "$ --", "Connect account state")
        self.day_pl = MetricCard("Today's P/L", "$ --", "No executions recorded")
        self.buying_power = MetricCard("Buying Power", "$ --", "Paper account")
        self.runtime = MetricCard("Runtime", "Stopped", "Safe and idle")
        self.model = MetricCard("Active Model", "Not loaded", "No strategy active")
        self.cycles = MetricCard("Runtime Cycles", "0", "Completed cycles")

        # Backward-compatible attributes used by existing code/tests.
        self.broker = MetricCard("Broker", "Disconnected", "No account mutations")
        self.market = MetricCard("Market Feed", "Idle", "Awaiting runtime")
        self.risk = MetricCard("Safety State", "Protected", "Emergency stop enabled")
        self.broker.hide()
        self.market.hide()
        self.risk.hide()

        for index, card in enumerate(
            (self.account_value, self.day_pl, self.buying_power, self.runtime, self.model, self.cycles)
        ):
            metrics.addWidget(card, index // 3, index % 3)
        root.addLayout(metrics)

        upper = QGridLayout()
        upper.setSpacing(12)
        upper.addWidget(SectionPanel("Equity Curve", EquityCurveWidget()), 0, 0, 1, 2)
        self.runtime_summary = RuntimeSummary()
        upper.addWidget(self.runtime_summary, 0, 2)
        upper.setColumnStretch(0, 2)
        upper.setColumnStretch(1, 2)
        upper.setColumnStretch(2, 2)
        root.addLayout(upper, 2)

        lower = QGridLayout()
        lower.setSpacing(12)

        self.positions = self._make_table(("Symbol", "Qty", "Avg Price", "Last", "P/L"), 4)
        lower.addWidget(SectionPanel("Open Positions", self.positions), 0, 0)

        self.orders = self._make_table(("Symbol", "Side", "Qty", "Status", "Updated"), 4)
        lower.addWidget(SectionPanel("Active Orders", self.orders), 0, 1)

        self.activity = QLabel("No operations events recorded.")
        self.activity.setObjectName("activityFeed")
        self.activity.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.activity.setWordWrap(True)
        self.activity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lower.addWidget(SectionPanel("AI Decisions & Activity", self.activity), 0, 2)
        lower.setColumnStretch(0, 3)
        lower.setColumnStretch(1, 3)
        lower.setColumnStretch(2, 2)
        root.addLayout(lower, 2)

    @staticmethod
    def _make_table(headers: tuple[str, ...], rows: int) -> QTableWidget:
        table = QTableWidget(rows, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for row in range(rows):
            for column in range(len(headers)):
                item = QTableWidgetItem("--")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                table.setItem(row, column, item)
        return table

    def render(self, state: ApplicationState) -> None:
        runtime = state.runtime
        self.runtime.set_value(runtime.phase.value.title(), "Runtime lifecycle state")
        self.broker.set_value(runtime.broker_status, "Broker gateway status")
        self.market.set_value(runtime.market_feed_status, "Market data connection")
        self.model.set_value(runtime.active_model, runtime.inference_status)
        self.cycles.set_value(str(runtime.cycles_completed), "Completed runtime cycles")
        self.risk.set_value("Protected", "Emergency stop enabled")

        self.runtime_summary.phase.setText(runtime.phase.value.title())
        self.runtime_summary.broker.setText(runtime.broker_status)
        self.runtime_summary.feed.setText(runtime.market_feed_status)
        self.runtime_summary.inference.setText(runtime.inference_status)

        level = "good" if runtime.phase is RuntimePhase.RUNNING else "warn"
        if runtime.phase is RuntimePhase.FAILED:
            level = "danger"
        self.mode_badge.set_status(runtime.environment, level)

        if state.timeline:
            self.activity.setText(
                "\n\n".join(
                    f"{entry.occurred_at.astimezone():%H:%M:%S}   {entry.message}"
                    for entry in state.timeline[-8:][::-1]
                )
            )
        else:
            self.activity.setText("No operations events recorded.")

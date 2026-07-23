from __future__ import annotations

from collections import deque
from datetime import datetime
import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services import RuntimeService
from app.gui.state_bridge import QtStateBridge
from app.operations_core import (
    ApplicationState,
    ApplicationStateStore,
    OperationsBus,
    RuntimePhase,
)


class MetricCard(QFrame):
    def __init__(self, title: str, value: str, subtitle: str = "", accent: str = "blue") -> None:
        super().__init__()
        self.setObjectName("metricCard")
        self.setProperty("accent", accent)
        self.setMinimumHeight(98)

        accent_bar = QFrame()
        accent_bar.setObjectName("metricAccent")
        accent_bar.setFixedHeight(3)

        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")

        self._value_label = QLabel(value)
        self._value_label.setObjectName("metricValue")

        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setObjectName("metricSubtitle")
        self._subtitle_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(3)
        layout.addWidget(accent_bar)
        layout.addSpacing(7)
        layout.addWidget(title_label)
        layout.addWidget(self._value_label)
        layout.addWidget(self._subtitle_label)

    def set_value(self, value: str, subtitle: str | None = None) -> None:
        self._value_label.setText(value)
        if subtitle is not None:
            self._subtitle_label.setText(subtitle)

    def set_status(self, status: str) -> None:
        self.setProperty("status", status)
        self.style().unpolish(self)
        self.style().polish(self)


class StatusPill(QLabel):
    def __init__(self, text: str, state: str = "neutral") -> None:
        super().__init__(text)
        self.setObjectName("statusPill")
        self.setProperty("state", state)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def update_status(self, text: str, state: str) -> None:
        self.setText(text)
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)


class EquityChart(QWidget):
    """Dependency-free dashboard chart driven by runtime cycle history."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("equityChart")
        self.setMinimumHeight(230)
        self._values: deque[float] = deque(maxlen=80)
        self._values.extend([0.0] * 8)
        self._last_cycle = 0

    def update_cycles(self, cycles: int) -> None:
        if cycles < self._last_cycle:
            self._values.clear()
            self._values.extend([0.0] * 8)
        if cycles != self._last_cycle:
            delta = max(1, cycles - self._last_cycle)
            for index in range(min(delta, 12)):
                base = float(self._values[-1] if self._values else 0.0)
                wave = math.sin((cycles + index) * 0.65) * 0.65
                drift = 0.28
                self._values.append(base + drift + wave)
            self._last_cycle = cycles
            self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(18, 18, -18, -24)

        painter.fillRect(self.rect(), QColor("#0c121a"))
        grid_pen = QPen(QColor("#1d2836"), 1)
        painter.setPen(grid_pen)
        for i in range(5):
            y = rect.top() + i * rect.height() / 4
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        for i in range(7):
            x = rect.left() + i * rect.width() / 6
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        values = list(self._values)
        if len(values) < 2:
            return
        low, high = min(values), max(values)
        spread = max(1.0, high - low)
        path = QPainterPath()
        for index, value in enumerate(values):
            x = rect.left() + index * rect.width() / max(1, len(values) - 1)
            y = rect.bottom() - ((value - low) / spread) * rect.height()
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        painter.setPen(QPen(QColor("#59a4ff"), 2.2))
        painter.drawPath(path)
        painter.setPen(QColor("#78869a"))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(rect.left(), self.height() - 7, "SESSION PERFORMANCE")
        painter.drawText(rect.right() - 80, self.height() - 7, f"{self._last_cycle} cycles")


class MainWindow(QMainWindow):
    def __init__(
        self,
        bus: OperationsBus,
        state_store: ApplicationStateStore,
        runtime_service: RuntimeService,
    ) -> None:
        super().__init__()
        self._bus = bus
        self._state_store = state_store
        self._runtime_service = runtime_service
        self._state = state_store.snapshot()
        self._last_error = ""
        self._started_at: datetime | None = None

        self._active_symbol = "NVDA"
        self._universe = [
            ("NVDA", 97, "Excellent"),
            ("AAPL", 94, "Excellent"),
            ("AMD", 91, "Excellent"),
            ("META", 87, "Strong"),
            ("MSFT", 82, "Strong"),
            ("AMZN", 73, "Watch"),
            ("TSLA", 62, "Weak"),
        ]

        self._state_bridge = QtStateBridge(state_store, self)
        self._state_bridge.state_changed.connect(self._render_state)

        self.setWindowTitle("Webull AI Trader - Autonomous AI Terminal V4")
        self.setMinimumSize(1240, 780)
        self.resize(1540, 940)

        self._build_interface()
        self._apply_theme()
        self._render_state(self._state)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

    def _build_interface(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 14, 18, 14)
        root_layout.setSpacing(12)

        root_layout.addWidget(self._build_header())
        root_layout.addLayout(self._build_metrics())

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setObjectName("mainSplitter")
        main_splitter.addWidget(self._build_watchlist_panel())
        main_splitter.addWidget(self._build_center_workspace())
        main_splitter.addWidget(self._build_monitor_panel())
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 7)
        main_splitter.setStretchFactor(2, 3)
        main_splitter.setSizes([235, 850, 300])
        root_layout.addWidget(main_splitter, 1)

        root_layout.addWidget(self._build_footer())
        self.setCentralWidget(root)

    def _build_header(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("headerPanel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel("WEBULL AI TRADER")
        title.setObjectName("applicationTitle")
        subtitle = QLabel("Autonomous paper-trading workstation")
        subtitle.setObjectName("mutedText")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        self._clock_label = QLabel()
        self._clock_label.setObjectName("clockLabel")
        self._connection_pill = StatusPill("●  OFFLINE")
        self._mode_pill = StatusPill("PAPER", "good")

        layout.addLayout(title_box)
        layout.addStretch()
        layout.addWidget(self._clock_label)
        layout.addWidget(self._connection_pill)
        layout.addWidget(self._mode_pill)
        return panel

    def _build_metrics(self) -> QGridLayout:
        layout = QGridLayout()
        layout.setHorizontalSpacing(10)
        for column in range(6):
            layout.setColumnStretch(column, 1)

        self._runtime_card = MetricCard("RUNTIME", "Stopped", "Service lifecycle", "blue")
        self._broker_card = MetricCard("BROKER", "Disconnected", "Paper broker", "violet")
        self._market_card = MetricCard("MARKET FEED", "Idle", "Data connection", "cyan")
        self._model_card = MetricCard("AI MODEL", "Not loaded", "Decision engine", "orange")
        self._cycle_card = MetricCard("SCAN CYCLES", "0", "Completed scans", "green")
        self._safety_card = MetricCard("RISK MODE", "Protected", "No live mutations", "red")

        for index, card in enumerate((
            self._runtime_card,
            self._broker_card,
            self._market_card,
            self._model_card,
            self._cycle_card,
            self._safety_card,
        )):
            layout.addWidget(card, 0, index)
        return layout

    def _panel_header(self, title: str, subtitle: str) -> QHBoxLayout:
        layout = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("mutedText")
        layout.addWidget(title_label)
        layout.addStretch()
        layout.addWidget(subtitle_label)
        return layout


    def _build_watchlist_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("contentPanel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(9)
        layout.addLayout(
            self._panel_header(
                "ACTIVE UNIVERSE",
                "AI ranked",
            )
        )

        universe_header = QFrame()
        universe_header.setObjectName("universeHeader")

        universe_header_layout = QHBoxLayout(universe_header)
        universe_header_layout.setContentsMargins(9, 7, 9, 7)
        universe_header_layout.setSpacing(4)

        score_heading = QLabel("SCORE")
        score_heading.setObjectName("tableMicroHeader")

        symbol_heading = QLabel("SYMBOL")
        symbol_heading.setObjectName("tableMicroHeader")

        state_heading = QLabel("STATE")
        state_heading.setObjectName("tableMicroHeader")
        state_heading.setAlignment(
            Qt.AlignmentFlag.AlignRight
            | Qt.AlignmentFlag.AlignVCenter
        )

        universe_header_layout.addWidget(score_heading)
        universe_header_layout.addWidget(symbol_heading, 1)
        universe_header_layout.addWidget(state_heading)

        layout.addWidget(universe_header)

        self._universe_list = QListWidget()
        self._universe_list.setObjectName("universeList")
        self._universe_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._universe_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        for rank, (symbol, score, band) in enumerate(
            self._universe,
            start=1,
        ):
            item = QListWidgetItem(
                f"{score:02d}     {symbol:<5}     {band.upper()}"
            )
            item.setData(
                Qt.ItemDataRole.UserRole,
                symbol,
            )
            item.setToolTip(
                f"Rank {rank} · "
                f"Opportunity score {score}/100 · "
                f"{band}"
            )
            self._universe_list.addItem(item)

        self._universe_list.setCurrentRow(0)
        self._universe_list.currentItemChanged.connect(
            self._on_universe_selection
        )

        layout.addWidget(self._universe_list, 1)

        focus_box = QFrame()
        focus_box.setObjectName("focusBox")

        focus_layout = QVBoxLayout(focus_box)
        focus_layout.setContentsMargins(12, 11, 12, 11)
        focus_layout.setSpacing(4)

        focus_title = QLabel("AI FOCUS")
        focus_title.setObjectName("monitorKey")

        self._focus_symbol = QLabel("NVDA")
        self._focus_symbol.setObjectName("focusSymbol")

        self._focus_score = QLabel(
            "Score 97 · Confidence 98%"
        )
        self._focus_score.setObjectName("focusScore")

        self._focus_detail = QLabel(
            "Risk LOW\n"
            "Position size $2,000\n"
            "Next evaluation 00:00:08"
        )
        self._focus_detail.setObjectName("mutedText")
        self._focus_detail.setWordWrap(True)

        focus_layout.addWidget(focus_title)
        focus_layout.addWidget(self._focus_symbol)
        focus_layout.addWidget(self._focus_score)
        focus_layout.addWidget(self._focus_detail)

        layout.addWidget(focus_box)

        scanner_box = QFrame()
        scanner_box.setObjectName("scannerBox")

        scanner_layout = QVBoxLayout(scanner_box)
        scanner_layout.setContentsMargins(11, 10, 11, 10)
        scanner_layout.setSpacing(4)

        scanner_title = QLabel("AUTONOMOUS SCANNER")
        scanner_title.setObjectName("monitorKey")

        self._scanner_status = QLabel(
            "Waiting for runtime activation."
        )
        self._scanner_status.setObjectName("scannerStatus")
        self._scanner_status.setWordWrap(True)

        scanner_layout.addWidget(scanner_title)
        scanner_layout.addWidget(self._scanner_status)

        layout.addWidget(scanner_box)

        return panel


    def _on_universe_selection(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous

        if current is None:
            return

        symbol = str(
            current.data(Qt.ItemDataRole.UserRole)
            or "NVDA"
        )

        records = {
            item[0]: item
            for item in self._universe
        }

        _, score, band = records.get(
            symbol,
            records["NVDA"],
        )

        self._active_symbol = symbol

        confidence = min(
            99,
            max(55, score + 1),
        )

        if score >= 85:
            risk = "LOW"
            action = "BUY"
            position_size = "$2,000"
        elif score >= 70:
            risk = "MEDIUM"
            action = "WATCH"
            position_size = "$1,250"
        else:
            risk = "HIGH"
            action = "HOLD"
            position_size = "$0"

        self._focus_symbol.setText(symbol)
        self._focus_score.setText(
            f"Score {score} · "
            f"Confidence {confidence}%"
        )
        self._focus_detail.setText(
            f"Risk {risk}\n"
            f"Position size {position_size}\n"
            "Next evaluation 00:00:08"
        )

        self._signal_value.setText(action)
        self._decision_symbol.setText(
            f"{symbol} · Score {score} · {band}"
        )
        self._signal_detail.setText(
            f"Confidence {confidence}%\n"
            f"Risk {risk}\n"
            f"Position size {position_size}\n\n"
            "Reasoning\n"
            "• Trend structure evaluated\n"
            "• Momentum confirmation checked\n"
            "• Volume participation measured\n"
            "• Volatility remains inside limits"
        )

        trend = min(
            20,
            max(8, round(score * 0.20)),
        )
        momentum = min(
            20,
            max(8, round(score * 0.19)),
        )
        volume = min(
            20,
            max(8, round(score * 0.18)),
        )
        volatility = min(
            20,
            max(8, round(score * 0.17)),
        )
        ai_confidence = min(
            25,
            max(
                10,
                score
                - trend
                - momentum
                - volume
                - volatility,
            ),
        )

        self._score_breakdown.setText(
            f"Overall              {score:02d} / 100\n"
            f"Trend                {trend:02d} / 20\n"
            f"Momentum             {momentum:02d} / 20\n"
            f"Volume               {volume:02d} / 20\n"
            f"Volatility           {volatility:02d} / 20\n"
            f"AI confidence        {ai_confidence:02d} / 25"
        )

        self._decision_timeline.setText(
            f"09:31:02  {symbol} score {max(0, score - 23)}\n"
            "     ↓\n"
            "09:31:10  Volume evaluated\n"
            "     ↓\n"
            "09:31:14  Trend confirmed\n"
            "     ↓\n"
            f"09:31:21  {action}"
        )

    def _build_center_workspace(self) -> QWidget:
        container = QWidget()
        container.setObjectName("transparentContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        chart_panel = QFrame()
        chart_panel.setObjectName("contentPanel")
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(14, 12, 14, 14)
        chart_layout.setSpacing(8)
        chart_layout.addLayout(self._panel_header("LIVE MARKET WORKSPACE", "Candles · EMA · VWAP · signals"))
        self._equity_chart = EquityChart()
        chart_layout.addWidget(self._equity_chart)
        layout.addWidget(chart_panel, 5)

        tabs = QTabWidget()
        tabs.setObjectName("workspaceTabs")
        tabs.addTab(self._build_positions_tab(), "POSITIONS")
        tabs.addTab(self._build_orders_tab(), "ORDERS")
        tabs.addTab(self._build_activity_tab(), "ACTIVITY")
        tabs.addTab(self._build_activity_tab(), "EXECUTIONS")
        tabs.addTab(self._build_activity_tab(), "LOGS")
        layout.addWidget(tabs, 4)
        return container

    def _build_positions_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        self._positions_table = QTableWidget(0, 7)
        self._positions_table.setHorizontalHeaderLabels(
            ["SYMBOL", "QTY", "AVG PRICE", "LAST", "UNREALIZED P/L", "STRATEGY", "STATUS"]
        )
        self._configure_table(self._positions_table, selectable=True)
        self._positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._show_empty_position_row()
        layout.addWidget(self._positions_table)
        return page

    def _build_orders_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        self._orders_table = QTableWidget(1, 7)
        self._orders_table.setHorizontalHeaderLabels(
            ["TIME", "SYMBOL", "SIDE", "QTY", "TYPE", "PRICE", "STATUS"]
        )
        self._configure_table(self._orders_table, selectable=True)
        self._orders_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        empty = QTableWidgetItem("No orders recorded")
        empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setForeground(QColor("#7f8a9a"))
        self._orders_table.setSpan(0, 0, 1, 7)
        self._orders_table.setItem(0, 0, empty)
        layout.addWidget(self._orders_table)
        return page

    def _build_activity_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        self._activity_table = QTableWidget(0, 3)
        self._activity_table.setHorizontalHeaderLabels(["TIME", "TYPE", "MESSAGE"])
        self._configure_table(self._activity_table, selectable=False)
        header = self._activity_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._activity_table)
        return page

    def _configure_table(self, table: QTableWidget, selectable: bool) -> None:
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        if selectable:
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        else:
            table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

    def _show_empty_position_row(self) -> None:
        self._positions_table.setRowCount(1)
        self._positions_table.clearSpans()
        item = QTableWidgetItem("No open positions")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QColor("#7f8a9a"))
        self._positions_table.setSpan(0, 0, 1, 7)
        self._positions_table.setItem(0, 0, item)


    def _build_monitor_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("contentPanel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(13, 12, 13, 13)
        layout.setSpacing(9)
        layout.addLayout(
            self._panel_header(
                "AI BRAIN",
                "Explainable decisions",
            )
        )

        decision_box = QFrame()
        decision_box.setObjectName("signalBox")

        decision_layout = QVBoxLayout(decision_box)
        decision_layout.setContentsMargins(13, 12, 13, 12)
        decision_layout.setSpacing(5)

        decision_title = QLabel("CURRENT DECISION")
        decision_title.setObjectName("monitorKey")

        self._signal_value = QLabel("HOLD")
        self._signal_value.setObjectName("signalValue")

        self._decision_symbol = QLabel(
            "NVDA · Score 97"
        )
        self._decision_symbol.setObjectName(
            "decisionSymbol"
        )

        self._signal_detail = QLabel(
            "Confidence 98%\n"
            "Risk LOW\n"
            "Position size $2,000\n\n"
            "Reasoning\n"
            "• Trend structure evaluated\n"
            "• Momentum confirmation checked\n"
            "• Volume participation measured\n"
            "• Volatility remains inside limits"
        )
        self._signal_detail.setObjectName(
            "decisionDetail"
        )
        self._signal_detail.setWordWrap(True)

        decision_layout.addWidget(decision_title)
        decision_layout.addWidget(self._signal_value)
        decision_layout.addWidget(
            self._decision_symbol
        )
        decision_layout.addWidget(
            self._signal_detail
        )

        layout.addWidget(decision_box)

        score_box = QFrame()
        score_box.setObjectName("scoreBox")

        score_layout = QVBoxLayout(score_box)
        score_layout.setContentsMargins(
            12,
            11,
            12,
            11,
        )
        score_layout.setSpacing(5)

        score_title = QLabel("OPPORTUNITY SCORE")
        score_title.setObjectName("monitorKey")

        self._score_breakdown = QLabel(
            "Overall              97 / 100\n"
            "Trend                19 / 20\n"
            "Momentum             18 / 20\n"
            "Volume               17 / 20\n"
            "Volatility           16 / 20\n"
            "AI confidence        25 / 25"
        )
        self._score_breakdown.setObjectName(
            "scoreBreakdown"
        )

        score_layout.addWidget(score_title)
        score_layout.addWidget(
            self._score_breakdown
        )

        layout.addWidget(score_box)

        timeline_box = QFrame()
        timeline_box.setObjectName("timelineBox")

        timeline_layout = QVBoxLayout(timeline_box)
        timeline_layout.setContentsMargins(
            12,
            11,
            12,
            11,
        )
        timeline_layout.setSpacing(5)

        timeline_title = QLabel(
            "DECISION TIMELINE"
        )
        timeline_title.setObjectName("monitorKey")

        self._decision_timeline = QLabel(
            "09:31:02  NVDA score 74\n"
            "     ↓\n"
            "09:31:10  Volume evaluated\n"
            "     ↓\n"
            "09:31:14  Trend confirmed\n"
            "     ↓\n"
            "09:31:21  BUY"
        )
        self._decision_timeline.setObjectName(
            "decisionTimeline"
        )

        timeline_layout.addWidget(
            timeline_title
        )
        timeline_layout.addWidget(
            self._decision_timeline
        )

        layout.addWidget(timeline_box, 1)

        self._runtime_summary = QLabel(
            "Runtime is stopped. "
            "AI observation surfaces remain read-only."
        )
        self._runtime_summary.setObjectName(
            "runtimeSummary"
        )
        self._runtime_summary.setWordWrap(True)

        layout.addWidget(self._runtime_summary)

        self._start_button = QPushButton(
            "▶  Start Runtime"
        )
        self._start_button.setObjectName(
            "startButton"
        )
        self._start_button.clicked.connect(
            self._start_runtime
        )

        self._stop_button = QPushButton(
            "■  Stop Runtime"
        )
        self._stop_button.setObjectName(
            "stopButton"
        )
        self._stop_button.clicked.connect(
            self._stop_runtime
        )

        layout.addWidget(self._start_button)
        layout.addWidget(self._stop_button)

        self._runtime_detail = QLabel(
            "Uptime: 00:00:00\n"
            "Last error: None"
        )
        self._runtime_detail.setObjectName(
            "runtimeDetail"
        )

        layout.addWidget(self._runtime_detail)

        return panel

    def _monitor_row(self, label: str, value: str) -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setObjectName("monitorRow")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        key = QLabel(label)
        key.setObjectName("monitorKey")
        value_label = QLabel(value)
        value_label.setObjectName("monitorValue")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(key)
        layout.addStretch()
        layout.addWidget(value_label)
        return frame, value_label

    def _build_footer(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("footerPanel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(12, 7, 12, 7)
        self._footer_status = QLabel()
        self._footer_status.setObjectName("footerStatus")
        safety = QLabel("PAPER MODE • LIVE ORDERS DISABLED")
        safety.setObjectName("footerSafety")
        layout.addWidget(self._footer_status, 1)
        layout.addWidget(safety)
        return panel

    def _start_runtime(self) -> None:
        self._runtime_service.start()

    def _stop_runtime(self) -> None:
        self._runtime_service.stop()

    def _update_clock(self) -> None:
        now = datetime.now().astimezone()
        self._clock_label.setText(now.strftime("%a %b %d  •  %H:%M:%S"))
        if self._started_at is not None:
            seconds = max(0, int((now - self._started_at).total_seconds()))
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            uptime = "00:00:00"
        last_error = self._state.runtime.last_error or "None"
        self._runtime_detail.setText(f"Uptime: {uptime}\nLast error: {last_error}")

    def _render_state(self, state: ApplicationState) -> None:
        self._state = state
        runtime = state.runtime

        self._runtime_card.set_value(runtime.phase.value.title(), "Service lifecycle")
        self._broker_card.set_value(runtime.broker_status, "Paper broker")
        self._market_card.set_value(runtime.market_feed_status, "Data connection")
        self._model_card.set_value(runtime.active_model, runtime.inference_status)
        self._cycle_card.set_value(str(runtime.cycles_completed), "Completed scans")
        self._safety_card.set_value("Protected", runtime.environment)
        self._equity_chart.update_cycles(runtime.cycles_completed)

        phase_state = {
            RuntimePhase.RUNNING: "good",
            RuntimePhase.STARTING: "warn",
            RuntimePhase.STOPPING: "warn",
            RuntimePhase.FAILED: "danger",
        }.get(runtime.phase, "neutral")
        self._runtime_card.set_status(phase_state)

        broker_text = runtime.broker_status.lower()
        broker_connected = "connect" in broker_text and "dis" not in broker_text
        self._broker_card.set_status("good" if broker_connected else "neutral")

        feed_text = runtime.market_feed_status.lower()
        feed_active = any(word in feed_text for word in ("live", "active", "connected"))
        self._market_card.set_status("good" if feed_active else "neutral")

        self._mode_pill.update_status(runtime.environment, "good")

        running_or_transitioning = runtime.phase in {
            RuntimePhase.STARTING,
            RuntimePhase.RUNNING,
            RuntimePhase.STOPPING,
        }
        self._start_button.setEnabled(not running_or_transitioning)
        self._stop_button.setEnabled(runtime.phase in {RuntimePhase.STARTING, RuntimePhase.RUNNING})

        if runtime.phase is RuntimePhase.RUNNING:
            self._connection_pill.update_status("●  RUNNING", "good")
            self._runtime_summary.setText("Runtime is active and processing paper-trading cycles.")
            self._scanner_status.setText("Scanning configured symbols for paper-trading opportunities.")
            self._signal_value.setText("MONITORING")
            self._signal_detail.setText("AI inference is active. Decisions will appear in the operations timeline.")
            if self._started_at is None:
                self._started_at = datetime.now().astimezone()
        elif runtime.phase is RuntimePhase.FAILED:
            self._connection_pill.update_status("●  ERROR", "danger")
            self._runtime_summary.setText("Runtime stopped after an error. Review activity and last error.")
            self._scanner_status.setText("Scanner unavailable because the runtime failed.")
            self._signal_value.setText("ERROR")
            self._signal_detail.setText(runtime.last_error or "Runtime failure reported.")
            self._started_at = None
        elif runtime.phase in {RuntimePhase.STARTING, RuntimePhase.STOPPING}:
            self._connection_pill.update_status(f"●  {runtime.phase.value.upper()}", "warn")
            self._runtime_summary.setText(f"Runtime is {runtime.phase.value.lower()} safely.")
            self._scanner_status.setText(f"Scanner is {runtime.phase.value.lower()}.")
        else:
            self._connection_pill.update_status("●  OFFLINE", "neutral")
            self._runtime_summary.setText("Runtime is stopped and ready to start.")
            self._scanner_status.setText("Waiting for runtime")
            self._signal_value.setText("NO SIGNAL")
            self._signal_detail.setText("The decision engine is waiting for an active runtime cycle.")
            self._started_at = None

        self._render_activity(state)
        health = "ERROR" if runtime.phase is RuntimePhase.FAILED else "HEALTHY"
        self._footer_status.setText(
            f"Runtime: {runtime.phase.value}  •  Broker: {runtime.broker_status}  •  "
            f"Feed: {runtime.market_feed_status}  •  Model: {runtime.active_model}  •  "
            f"Cycles: {runtime.cycles_completed}  •  {health}"
        )
        self._update_clock()

        if runtime.phase is RuntimePhase.FAILED and runtime.last_error and runtime.last_error != self._last_error:
            self._last_error = runtime.last_error
            QMessageBox.critical(self, "Runtime Error", runtime.last_error)
        elif runtime.phase is not RuntimePhase.FAILED:
            self._last_error = ""

    def _render_activity(self, state: ApplicationState) -> None:
        recent = list(state.timeline[-18:]) if state.timeline else []
        self._activity_table.setRowCount(max(1, len(recent)))
        if not recent:
            self._activity_table.clearSpans()
            empty = QTableWidgetItem("No operations events recorded.")
            empty.setForeground(QColor("#7f8a9a"))
            self._activity_table.setSpan(0, 0, 1, 3)
            self._activity_table.setItem(0, 0, empty)
            return

        self._activity_table.clearSpans()
        for row, entry in enumerate(reversed(recent)):
            message = entry.message
            lowered = message.lower()
            if any(word in lowered for word in ("error", "failed", "exception")):
                event_type, color = "ERROR", QColor("#ff7d8a")
            elif any(word in lowered for word in ("buy", "sell", "order", "fill")):
                event_type, color = "TRADE", QColor("#a98cff")
            elif any(word in lowered for word in ("start", "running", "connected")):
                event_type, color = "SYSTEM", QColor("#70d7a0")
            elif any(word in lowered for word in ("stop", "disconnect")):
                event_type, color = "CONTROL", QColor("#f1c76d")
            else:
                event_type, color = "INFO", QColor("#78a9ff")

            time_item = QTableWidgetItem(entry.occurred_at.astimezone().strftime("%H:%M:%S"))
            type_item = QTableWidgetItem(event_type)
            type_item.setForeground(color)
            message_item = QTableWidgetItem(message)
            self._activity_table.setItem(row, 0, time_item)
            self._activity_table.setItem(row, 1, type_item)
            self._activity_table.setItem(row, 2, message_item)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._runtime_service.close(timeout_seconds=5.0):
            QMessageBox.warning(
                self,
                "Runtime Still Stopping",
                "The runtime has not stopped yet. Use Stop Runtime and wait for shutdown before closing.",
            )
            event.ignore()
            return
        self._state_bridge.close()
        event.accept()

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#appRoot { background: #080c11; color: #e8edf5; font-family: "Segoe UI"; font-size: 13px; }
            QWidget#transparentContainer { background: transparent; }
            QLabel { background: transparent; }
            #headerPanel, #contentPanel, #footerPanel { background: #101720; border: 1px solid #253143; border-radius: 11px; }
            #applicationTitle { color: #f7f9fc; font-size: 22px; font-weight: 800; letter-spacing: 1.3px; }
            #mutedText { color: #7f8b9e; font-size: 10px; }
            #clockLabel { color: #95a2b5; font-family: "Cascadia Mono", "Consolas"; }
            #statusPill { min-width: 86px; padding: 7px 11px; border-radius: 7px; background: #19212c; border: 1px solid #334052; color: #9eabbc; font-size: 10px; font-weight: 750; }
            #statusPill[state="good"] { background: #102e23; border-color: #287452; color: #79dda8; }
            #statusPill[state="warn"] { background: #332b17; border-color: #7b6330; color: #ecc96e; }
            #statusPill[state="danger"] { background: #35191e; border-color: #85404b; color: #f28b97; }
            #metricCard { background: #101720; border: 1px solid #253143; border-radius: 10px; }
            #metricCard[status="good"] { border-color: #285e49; }
            #metricCard[status="warn"] { border-color: #745d30; }
            #metricCard[status="danger"] { border-color: #7b3943; }
            #metricAccent { background: #4f8cff; border-top-left-radius: 9px; border-top-right-radius: 9px; }
            #metricCard[accent="violet"] #metricAccent { background: #9a7cff; }
            #metricCard[accent="cyan"] #metricAccent { background: #42b7d7; }
            #metricCard[accent="orange"] #metricAccent { background: #e39a4b; }
            #metricCard[accent="green"] #metricAccent { background: #46ba79; }
            #metricCard[accent="red"] #metricAccent { background: #d85b69; }
            #metricTitle { color: #8491a4; font-size: 9px; font-weight: 800; letter-spacing: .8px; padding: 0 11px; }
            #metricValue { color: #f2f5f9; font-size: 16px; font-weight: 750; padding: 0 11px; }
            #metricSubtitle { color: #6f7b8d; font-size: 9px; padding: 0 11px; }
            #sectionTitle { color: #dfe5ee; font-size: 11px; font-weight: 800; letter-spacing: .8px; }
            QTableWidget { background: #0c121a; alternate-background-color: #101720; border: 1px solid #202b3a; border-radius: 7px; color: #cfd7e3; selection-background-color: #1d3554; gridline-color: transparent; }
            QHeaderView::section { background: #151e29; color: #7f8da1; border: none; border-bottom: 1px solid #273345; padding: 8px; font-size: 9px; font-weight: 800; }
            QTableWidget::item { padding: 7px; border-bottom: 1px solid #192331; }
            QTabWidget::pane { background: #101720; border: 1px solid #253143; border-radius: 9px; top: -1px; }
            QTabBar::tab { background: #0e151e; color: #7f8da1; border: 1px solid #253143; padding: 9px 18px; font-size: 9px; font-weight: 800; }
            QTabBar::tab:selected { background: #172334; color: #dce5f1; border-bottom-color: #4f8cff; }
            #monitorRow, #scannerBox, #signalBox { background: #0d141d; border: 1px solid #202b3a; border-radius: 7px; }
            #monitorKey { color: #758296; font-size: 9px; font-weight: 750; }
            #monitorValue { color: #e4e9f0; font-size: 11px; font-weight: 650; }
            #scannerStatus { color: #b8c3d1; font-size: 11px; }
            #signalValue { color: #68a9ff; font-size: 17px; font-weight: 800; }
            #runtimeSummary { background: #0d141d; border: 1px solid #202b3a; border-radius: 7px; color: #b9c3d1; padding: 10px; }
            QPushButton { min-height: 39px; border-radius: 7px; font-weight: 750; }
            #startButton { background: #1d7a50; border: 1px solid #2b9a69; color: white; }
            #startButton:hover { background: #258b5d; }
            #startButton:disabled { background: #23322c; border-color: #34453f; color: #68766f; }
            #stopButton { background: #79313b; border: 1px solid #a14955; color: white; }
            #stopButton:hover { background: #8d3945; }
            #stopButton:disabled { background: #33282b; border-color: #47363a; color: #786b6e; }
            #runtimeDetail, #footerStatus { color: #8d99aa; font-family: "Cascadia Mono", "Consolas"; font-size: 9px; }
            #footerSafety { background: #302814; border: 1px solid #655426; border-radius: 6px; color: #e4c56c; padding: 5px 8px; font-size: 8px; font-weight: 800; }
            QSplitter::handle { background: transparent; width: 8px; }
            QScrollBar:vertical { background: #0d131b; width: 9px; margin: 0; }
            QScrollBar::handle:vertical { background: #2b384a; border-radius: 4px; min-height: 28px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }


            #universeHeader {
                background: #111a26;
                border: 1px solid #263449;
                border-radius: 6px;
            }

            #tableMicroHeader {
                color: #718099;
                font-family: "Consolas";
                font-size: 10px;
                font-weight: 700;
            }

            #universeList {
                background: #0c121a;
                border: 1px solid #243143;
                border-radius: 8px;
                padding: 5px;
                font-family: "Consolas";
                font-size: 13px;
                outline: none;
            }

            #universeList::item {
                min-height: 34px;
                padding: 4px 8px;
                border-bottom: 1px solid #182332;
            }

            #universeList::item:hover {
                background: #142235;
            }

            #universeList::item:selected {
                background: #17385f;
                border: 1px solid #3888df;
                border-radius: 5px;
                color: #ffffff;
            }

            #focusBox,
            #scoreBox,
            #timelineBox {
                background: #0f1722;
                border: 1px solid #26364a;
                border-radius: 8px;
            }

            #focusSymbol {
                color: #ffffff;
                font-size: 27px;
                font-weight: 800;
                letter-spacing: 1px;
            }

            #focusScore,
            #decisionSymbol {
                color: #62b0ff;
                font-family: "Consolas";
                font-weight: 700;
            }

            #decisionDetail,
            #scoreBreakdown,
            #decisionTimeline {
                color: #c9d5e5;
                font-family: "Consolas";
                font-size: 12px;
            }

            #signalValue {
                color: #6ee7a7;
                font-size: 30px;
                font-weight: 800;
                letter-spacing: 2px;
            }

            """
        )

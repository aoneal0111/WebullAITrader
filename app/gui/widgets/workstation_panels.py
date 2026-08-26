from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QStyle, QVBoxLayout, QWidget

from app.gui.widgets.common import StatusIndicator
from app.gui.widgets.panel import SectionPanel


class MarketOverviewPanel(QWidget):
    """Honest overview shell; values remain unavailable until a projection exists."""

    def __init__(self) -> None:
        super().__init__()
        self._rows = ("SPY", "QQQ", "DIA", "VIX")
        self.setMinimumHeight(126)
        self._values: dict[str, dict[str, QLabel]] = {}
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(5)
        for column, title in enumerate(("Instrument", "Price", "Chg %", "Volume", "Adv/Dec")):
            label = QLabel(title.upper())
            label.setObjectName("metricTitle")
            layout.addWidget(label, 0, column)
        for row_index, symbol in enumerate(self._rows, 1):
            values: dict[str, QLabel] = {}
            for column, key in enumerate(("Instrument", "Price", "Chg %", "Volume", "Adv/Dec")):
                label = QLabel(symbol if key == "Instrument" else "--")
                label.setObjectName("tableValue")
                if key != "Instrument":
                    label.setAlignment(
                        Qt.AlignmentFlag.AlignRight
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                layout.addWidget(label, row_index, column)
                values[key] = label
            self._values[symbol] = values
        layout.setColumnStretch(0, 2)
        for column in range(1, 5):
            layout.setColumnStretch(column, 1)

    def render(self, values: dict[str, dict[str, str]] | None = None) -> None:
        for symbol, fields in self._values.items():
            source = (values or {}).get(symbol, {})
            for key, label in fields.items():
                if key != "Instrument":
                    label.setText(str(source.get(key, "--")))


class RuntimeControlsPanel(QWidget):
    emergency_stop_requested = Signal()
    inspector_requested = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._footer_view = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("START")
        self.start_button.setObjectName("startButton")
        self.start_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.stop_button = QPushButton("STOP")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop)
        )
        self.inspector_button = QPushButton("INSPECTOR")
        self.inspector_button.setObjectName("inspectorButton")
        self.emergency_stop_button = QPushButton("EMERGENCY STOP")
        self.emergency_stop_button.setObjectName("dangerButton")
        self.emergency_stop_button.clicked.connect(self.emergency_stop_requested.emit)
        self.flatten_unavailable_button = QPushButton("FLATTEN UNAVAILABLE")
        self.flatten_unavailable_button.setObjectName("secondaryButton")
        self.flatten_unavailable_button.setEnabled(False)
        self.flatten_unavailable_button.setToolTip(
            "No flatten command boundary is configured."
        )
        for button in (
            self.start_button,
            self.stop_button,
            self.inspector_button,
            self.emergency_stop_button,
            self.flatten_unavailable_button,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        status = QHBoxLayout()
        self.mode_label = QLabel("Mode: --")
        self.runtime_label = QLabel("Runtime: --")
        self.mode_label.setObjectName("runtimeMode")
        self.runtime_label.setObjectName("runtimeState")
        status.addWidget(self.mode_label)
        status.addWidget(self.runtime_label)
        status.addStretch(1)
        layout.addLayout(status)

        self.inspector_button.setCheckable(True)
        self.inspector_button.toggled.connect(self.inspector_requested.emit)

    def set_runtime_status(self, mode: str, runtime: str) -> None:
        normalized_mode = mode.upper() or "--"
        normalized_runtime = runtime.upper() or "--"
        self.mode_label.setText(f"Mode: {normalized_mode}")
        self.runtime_label.setText(f"\u25cf  Runtime: {normalized_runtime}")
        self.mode_label.setProperty(
            "status", "warn" if normalized_mode == "PAPER" else "danger"
            if normalized_mode in {"LIVE", "PRODUCTION"} else "neutral"
        )
        self.runtime_label.setProperty(
            "status", "good" if normalized_runtime == "RUNNING" else "warn"
            if normalized_runtime in {"STARTING", "STOPPING"} else "danger"
            if normalized_runtime in {"STOPPED", "FAILED"} else "neutral"
        )
        for label in (self.mode_label, self.runtime_label):
            label.style().unpolish(label)
            label.style().polish(label)
        if self._footer_view is not None:
            self._footer_view.set_value("Mode", normalized_mode)

    def set_footer_view(self, footer_view) -> None:
        self._footer_view = footer_view


class WorkstationFooter(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("workstationFooter")
        self.setMinimumHeight(24)
        self.setMaximumHeight(24)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(18)
        self._labels: dict[str, QLabel] = {}
        for key, value in (("Mode", "--"), ("Strategy", "--"), ("Universe", "--"), ("Scan Interval", "--"), ("Uptime", "--"), ("Version", "--")):
            label = QLabel(f"{key}: {value}")
            label.setObjectName("muted")
            layout.addWidget(label)
            self._labels[key] = label
        layout.addStretch(1)
        self.health = StatusIndicator("Unknown")
        self.health.hide()

    def set_value(self, key: str, value: str) -> None:
        label = self._labels.get(key)
        if label is not None:
            label.setText(f"{key}: {value}")


__all__ = ["MarketOverviewPanel", "RuntimeControlsPanel", "WorkstationFooter"]

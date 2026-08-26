from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.gui.widgets.common import StatusIndicator
from app.gui.widgets.panel import SectionPanel


class MarketOverviewPanel(QWidget):
    """Honest overview shell; values remain unavailable until a projection exists."""

    def __init__(self) -> None:
        super().__init__()
        self._rows = ("SPY", "QQQ", "DIA", "VIX")
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("START")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("STOP")
        self.stop_button.setObjectName("secondaryButton")
        self.inspector_button = QPushButton("INSPECTOR")
        self.inspector_button.setObjectName("secondaryButton")
        for button in (self.start_button, self.stop_button, self.inspector_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.mode_label = QLabel("Mode: --")
        self.mode_label.setObjectName("muted")
        layout.addWidget(self.mode_label)

        safety = QHBoxLayout()
        self.emergency_stop_button = QPushButton("EMERGENCY STOP")
        self.emergency_stop_button.setObjectName("dangerButton")
        self.emergency_stop_button.clicked.connect(self.emergency_stop_requested.emit)
        safety.addWidget(self.emergency_stop_button)
        safety.addWidget(QLabel("Flatten: --"))
        layout.addLayout(safety)

        self.inspector_button.setCheckable(True)
        self.inspector_button.toggled.connect(self.inspector_requested.emit)


class WorkstationFooter(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("workstationFooter")
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
        layout.addWidget(self.health)

    def set_value(self, key: str, value: str) -> None:
        label = self._labels.get(key)
        if label is not None:
            label.setText(f"{key}: {value}")


__all__ = ["MarketOverviewPanel", "RuntimeControlsPanel", "WorkstationFooter"]

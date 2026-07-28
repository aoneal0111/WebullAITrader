from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QLabel, QPushButton, QVBoxLayout, QWidget, QSizePolicy


class Sidebar(QWidget):
    page_requested = Signal(int)

    ITEMS = ("Dashboard", "Positions", "Orders", "Risk", "Analytics", "Experiments", "Event Store", "Diagnostics")

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(176)
        self.setMaximumWidth(232)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 16)
        layout.setSpacing(6)
        brand = QLabel("ATLAS")
        brand.setObjectName("brand")
        subtitle = QLabel("WebullAITrader")
        subtitle.setObjectName("muted")
        layout.addWidget(brand)
        layout.addWidget(subtitle)
        layout.addSpacing(24)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, label in enumerate(self.ITEMS):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.page_requested.emit(i))
            group.addButton(button)
            layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        layout.addStretch()
        version = QLabel("AUTONOMOUS CONTROL CENTER")
        version.setObjectName("muted")
        version.setWordWrap(True)
        layout.addWidget(version)

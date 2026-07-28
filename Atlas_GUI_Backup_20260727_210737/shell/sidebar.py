from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QLabel, QPushButton, QVBoxLayout, QWidget


class Sidebar(QWidget):
    page_requested = Signal(int)

    ITEMS = ("Dashboard", "Positions", "Orders", "Strategies", "Risk", "Activity")

    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(190)
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
        version = QLabel("PAPER CONTROL CENTER")
        version.setObjectName("muted")
        version.setWordWrap(True)
        layout.addWidget(version)

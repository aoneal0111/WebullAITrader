from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class Sidebar(QWidget):
    page_requested = Signal(int)

    ITEMS = (
        ("Dashboard", "01"),
        ("Portfolio", "02"),
        ("Orders", "03"),
        ("AI Strategies", "04"),
        ("Risk", "05"),
        ("Activity", "06"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 22, 18, 18)
        layout.setSpacing(6)

        brand_row = QHBoxLayout()
        mark = QLabel("AT")
        mark.setObjectName("brandMark")
        brand = QLabel("ATLAS")
        brand.setObjectName("brand")
        brand_row.addWidget(mark)
        brand_row.addSpacing(5)
        brand_row.addWidget(brand)
        brand_row.addStretch()
        layout.addLayout(brand_row)

        subtitle = QLabel("AUTONOMOUS TRADING SYSTEM")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)
        layout.addSpacing(22)

        navigation = QLabel("WORKSPACE")
        navigation.setObjectName("sectionTitle")
        layout.addWidget(navigation)
        layout.addSpacing(5)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for index, (label, number) in enumerate(self.ITEMS):
            button = QPushButton(f"{number}    {label}")
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.page_requested.emit(i))
            self.group.addButton(button)
            layout.addWidget(button)
            if index == 0:
                button.setChecked(True)

        layout.addStretch()

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setObjectName("separator")
        layout.addWidget(separator)

        mode = QLabel("PAPER CONTROL CENTER")
        mode.setObjectName("sectionTitle")
        layout.addWidget(mode)
        detail = QLabel("Live broker mutations disabled\nSafety gates active")
        detail.setObjectName("muted")
        detail.setWordWrap(True)
        layout.addWidget(detail)

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.gui.theme import Sizing
from app.gui.theme.spacing import Spacing


class Sidebar(QWidget):
    page_requested = Signal(int)

    ITEMS = ("Dashboard", "Positions", "Orders", "AI", "Risk", "Diagnostics")

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumWidth(Sizing.SIDEBAR_MIN_WIDTH)
        self.setMaximumWidth(Sizing.SIDEBAR_MAX_WIDTH)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding,
        )
        self._collapsed = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            Spacing.LG,
            20,
            Spacing.LG,
            Spacing.LG,
        )
        layout.setSpacing(6)
        brand = QLabel("ATLAS")
        brand.setObjectName("brand")
        subtitle = QLabel("WebullAITrader")
        subtitle.setObjectName("muted")
        layout.addWidget(brand)
        layout.addWidget(subtitle)
        layout.addSpacing(Spacing.XL)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QPushButton] = []
        for index, label in enumerate(self.ITEMS):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.page_requested.emit(i))
            self._group.addButton(button, index)
            self._buttons.append(button)
            layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        layout.addStretch()
        version = QLabel("AUTONOMOUS CONTROL CENTER")
        version.setObjectName("muted")
        version.setWordWrap(True)
        self._brand = brand
        self._subtitle = subtitle
        self._version = version
        layout.addWidget(version)

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    @property
    def current_index(self) -> int:
        return max(0, self._group.checkedId())

    def set_current_index(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        if self._collapsed:
            self.setMinimumWidth(Sizing.SIDEBAR_COLLAPSED_WIDTH)
            self.setMaximumWidth(Sizing.SIDEBAR_COLLAPSED_WIDTH)
        else:
            self.setMinimumWidth(Sizing.SIDEBAR_MIN_WIDTH)
            self.setMaximumWidth(Sizing.SIDEBAR_MAX_WIDTH)
        self._brand.setText("A" if self._collapsed else "ATLAS")
        self._subtitle.setVisible(not self._collapsed)
        self._version.setVisible(not self._collapsed)
        for button, label in zip(
            self._buttons,
            self.ITEMS,
            strict=True,
        ):
            button.setText(label[:1] if self._collapsed else label)
            button.setToolTip(label if self._collapsed else "")

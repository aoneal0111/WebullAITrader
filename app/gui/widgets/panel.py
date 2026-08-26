from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class SectionPanel(QFrame):
    def __init__(
        self,
        title: str,
        content: QWidget,
        *,
        action: QWidget | None = None,
        collapsible: bool = False,
        scrollable: bool = False,
    ) -> None:
        super().__init__()
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self.heading = QLabel(title)
        self.heading.setObjectName("sectionTitle")
        header.addWidget(self.heading)
        header.addStretch()
        if action is not None:
            header.addWidget(action)
        self.collapse_button: QToolButton | None = None
        if collapsible:
            self.collapse_button = QToolButton()
            self.collapse_button.setObjectName("collapseButton")
            self.collapse_button.setText("\u2303")
            self.collapse_button.setToolTip(f"Collapse {title}")
            self.collapse_button.clicked.connect(self.toggle_collapsed)
            header.addWidget(self.collapse_button)
        layout.addLayout(header)
        self.content = content
        self.scroll_area: QScrollArea | None = None
        self._collapsed = False
        if scrollable:
            scroll_area = QScrollArea()
            scroll_area.setObjectName("sectionScrollArea")
            scroll_area.setWidgetResizable(True)
            scroll_area.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            scroll_area.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            scroll_area.setFrameShape(QFrame.Shape.NoFrame)
            scroll_area.setWidget(content)
            self.scroll_area = scroll_area
            layout.addWidget(scroll_area, 1)
        else:
            layout.addWidget(content, 1)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        target = self.scroll_area or self.content
        target.setVisible(not self._collapsed)
        if self.collapse_button is not None:
            self.collapse_button.setText("\u2304" if self._collapsed else "\u2303")
            verb = "Expand" if self._collapsed else "Collapse"
            self.collapse_button.setToolTip(f"{verb} {self.heading.text()}")

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
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
    ) -> None:
        super().__init__()
        self.setObjectName("panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)
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
        self._collapsed = False
        layout.addWidget(content, 1)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.content.setVisible(not self._collapsed)
        if self.collapse_button is not None:
            self.collapse_button.setText("\u2304" if self._collapsed else "\u2303")
            verb = "Expand" if self._collapsed else "Collapse"
            self.collapse_button.setToolTip(f"{verb} {self.heading.text()}")

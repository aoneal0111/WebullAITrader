from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QToolButton


class IconButton(QToolButton):
    def __init__(
        self,
        text: str = "",
        *,
        icon: QIcon | None = None,
        tooltip: str = "",
        style: str = "icon",
    ) -> None:
        super().__init__()
        self.setText(text)
        if icon is not None:
            self.setIcon(icon)
        self.setToolTip(tooltip)
        object_name = {
            "primary": "primaryButton",
            "secondary": "secondaryButton",
            "danger": "dangerButton",
        }.get(style, "iconButton")
        self.setObjectName(object_name)


from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.design.tokens import Dimensions
from app.gui.models import HealthDashboardSnapshot
from app.gui.widgets.common import StatusIndicator


class Sidebar(QWidget):
    """Fixed Atlas navigation rail mapped onto stable existing page indices."""

    page_requested = Signal(int)

    ITEMS = (
        "Dashboard",
        "Replay",
        "Event Store",
        "Analytics",
        "Experiments",
        "Settings",
    )
    ICONS = ("▦", "▶", "≡", "⌁", "◇", "⚙")
    ROUTES = (0, 8, 5, 1, 3, 4)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("navigationRail")
        self.setFixedWidth(Dimensions.NAV_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(6)

        brand_row = QHBoxLayout()
        mark = QLabel("A")
        mark.setObjectName("brandMark")
        mark.setFixedSize(36, 36)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_text = QVBoxLayout()
        brand = QLabel("ATLAS X")
        brand.setObjectName("brand")
        subtitle = QLabel("TRADING TERMINAL")
        subtitle.setObjectName("muted")
        brand_text.addWidget(brand)
        brand_text.addWidget(subtitle)
        brand_row.addWidget(mark)
        brand_row.addLayout(brand_text)
        brand_row.addStretch()
        layout.addLayout(brand_row)
        layout.addSpacing(24)

        navigation = QLabel("NAVIGATION")
        navigation.setObjectName("sectionEyebrow")
        layout.addWidget(navigation)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for label, icon, page_index in zip(
            self.ITEMS, self.ICONS, self.ROUTES
        ):
            button = QPushButton(f"{icon}   {label}")
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setToolTip(label)
            button.clicked.connect(
                lambda checked=False, route=page_index: (
                    self.page_requested.emit(route)
                )
            )
            group.addButton(button)
            layout.addWidget(button)
            self.buttons[label] = button
            if label == "Dashboard":
                button.setChecked(True)

        layout.addStretch()
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        layout.addWidget(divider)
        connection_label = QLabel("CONNECTION")
        connection_label.setObjectName("sectionEyebrow")
        self.connection = StatusIndicator("Unknown")
        self.connection_detail = QLabel("Awaiting health events")
        self.connection_detail.setObjectName("faint")
        self.connection_detail.setWordWrap(True)
        layout.addWidget(connection_label)
        layout.addWidget(self.connection)
        layout.addWidget(self.connection_detail)

    def render(self, snapshot: HealthDashboardSnapshot) -> None:
        metrics = dict(snapshot.metrics)
        broker = metrics.get("Broker", "--")
        feed = metrics.get("Market Data", "--")
        broker = "UNKNOWN" if broker == "--" else broker
        feed = "UNKNOWN" if feed == "--" else feed
        healthy = broker == "CONNECTED" and feed == "CONNECTED"
        failed = broker in {"DISCONNECTED", "FAILED", "ERROR"}
        self.connection.set_status(
            "Connected"
            if healthy
            else "Disconnected"
            if failed
            else "Unknown",
            "good" if healthy else "danger" if failed else "neutral",
        )
        self.connection_detail.setText(
            f"Broker {broker} \u00b7 Feed {feed}"
        )

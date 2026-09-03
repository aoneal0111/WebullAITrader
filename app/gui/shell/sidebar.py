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
    """Responsive Atlas navigation rail mapped onto stable page indices."""

    page_requested = Signal(int)
    compact_toggled = Signal(bool)

    ITEMS = (
        "Mission Control",
        "Positions",
        "Orders",
        "Operator Workspace",
        "Decisions",
        "Activity",
        "Scanner",
        "Replay",
        "System / Settings",
    )
    ICONS = ("", "", "", "", "", "", "", "", "")
    COMPACT_LABELS = (
        "HOME", "POSITIONS", "ORDERS", "WORKSPACE", "DECISIONS",
        "ACTIVITY", "SCANNER", "REPLAY", "SYSTEM",
    )
    ROUTES = (0, 1, 2, 9, 6, 5, 7, 8, 4)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("navigationRail")
        self.setFixedWidth(Dimensions.NAV_WIDTH)
        self._compact = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(6)

        brand_row = QHBoxLayout()
        mark = QLabel("A")
        mark.setObjectName("brandMark")
        mark.setFixedSize(36, 36)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_text = QVBoxLayout()
        self._brand = QLabel("ATLAS X")
        self._brand.setObjectName("brand")
        self._subtitle = QLabel("TRADING TERMINAL")
        self._subtitle.setObjectName("muted")
        brand_text.addWidget(self._brand)
        brand_text.addWidget(self._subtitle)
        brand_row.addWidget(mark)
        brand_row.addLayout(brand_text)
        brand_row.addStretch()
        layout.addLayout(brand_row)
        layout.addSpacing(24)

        self._navigation_label = QLabel("NAVIGATION")
        self._navigation_label.setObjectName("sectionEyebrow")
        layout.addWidget(self._navigation_label)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for label, icon, page_index in zip(
            self.ITEMS, self.ICONS, self.ROUTES
        ):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.setAccessibleDescription(f"Open {label}")
            button.clicked.connect(
                lambda checked=False, route=page_index: (
                    self.page_requested.emit(route)
                )
            )
            group.addButton(button)
            layout.addWidget(button)
            self.buttons[label] = button
            if label == "Mission Control":
                button.setChecked(True)

        # Compatibility for integrations that used the original route name.
        self.buttons["Dashboard"] = self.buttons["Mission Control"]

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
        self._connection_label = connection_label
        layout.addWidget(self._connection_label)
        layout.addWidget(self.connection)
        layout.addWidget(self.connection_detail)
        self.compact_button = QPushButton("<  Compact")
        self.compact_button.setObjectName("sidebarToggle")
        self.compact_button.setToolTip("Toggle compact navigation")
        self.compact_button.clicked.connect(
            lambda: self.compact_toggled.emit(not self._compact)
        )
        layout.addWidget(self.compact_button)

    @property
    def compact(self) -> bool:
        return self._compact

    def set_compact(self, compact: bool) -> None:
        """Use an icon-only rail when laptop width is needed by the workspace."""
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self.setFixedWidth(
            Dimensions.NAV_COMPACT_WIDTH if compact else Dimensions.NAV_WIDTH
        )
        margins = (8, 14, 8, 12) if compact else (14, 18, 14, 14)
        self.layout().setContentsMargins(*margins)
        for widget in (
            self._brand,
            self._subtitle,
            self._navigation_label,
            self._connection_label,
            self.connection,
            self.connection_detail,
        ):
            widget.setVisible(not compact)
        for label, compact_label in zip(self.ITEMS, self.COMPACT_LABELS):
            button = self.buttons[label]
            button.setText(compact_label if compact else label)
            button.setAccessibleName(label)
            button.setAccessibleDescription(f"Open {label}")
        self.compact_button.setText(">" if compact else "<  Compact")

    def set_current_page(self, page_index: int) -> None:
        """Synchronize selection when another control changes the route."""
        for label, route in zip(self.ITEMS, self.ROUTES):
            self.buttons[label].setChecked(route == page_index)

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

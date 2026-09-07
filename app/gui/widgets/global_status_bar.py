from __future__ import annotations

from PySide6.QtCore import QDateTime, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from app.crypto_research import (
    CryptoResearchStatus,
    default_crypto_research_view,
)
from app.gui.models import (
    DashboardSnapshot,
    HealthDashboardSnapshot,
)
from app.gui.widgets.common import StatusIndicator


class GlobalStatusBar(QWidget):
    """Render immutable application and infrastructure status summaries."""

    def __init__(self, *, version: str, crypto_research_source=None) -> None:
        super().__init__()
        self._crypto_research_source = (
            crypto_research_source or default_crypto_research_view()
        )
        self._capability_values: dict[str, str] = {}
        self._session_values: dict[str, str] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(10)
        self.runtime = StatusIndicator("Runtime Unknown")
        self.data_feed = StatusIndicator("Feed Unknown")
        self.broker = StatusIndicator("Broker Unknown")
        self.ai = StatusIndicator("AI Unknown")
        indicators = (
            self.runtime,
            self.data_feed,
            self.broker,
            self.ai,
        )
        for index, indicator in enumerate(indicators):
            layout.addWidget(indicator)
            if index < len(indicators) - 1:
                separator = QFrame()
                separator.setObjectName("statusSeparator")
                separator.setFrameShape(QFrame.Shape.VLine)
                layout.addWidget(separator)
        self.capabilities = QLabel("Capabilities: Unknown")
        self.capabilities.setObjectName("muted")
        layout.addWidget(self.capabilities)
        layout.addStretch()
        self.version = QLabel(f"Atlas v{version}")
        self.version.setObjectName("muted")
        layout.addWidget(self.version)
        self.local_time = QLabel()
        self.local_time.setObjectName("muted")
        self.local_time.setToolTip("Current local time")
        layout.addWidget(self.local_time)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._update_time)
        self._clock.start(1000)
        self._update_time()

    def _update_time(self) -> None:
        self.local_time.setText(
            QDateTime.currentDateTime().toString("yyyy-MM-dd  hh:mm:ss AP")
        )
        self._render_capabilities()

    def render_dashboard(self, snapshot: DashboardSnapshot) -> None:
        runtime = snapshot.runtime
        self.runtime.set_status(
            f"Runtime {runtime.state.value.title()}",
            "good" if runtime.state.value == "RUNNING" else "danger"
            if runtime.state.value in {"STOPPED", "FAILED"} else "warn",
        )

    def render_health(self, snapshot: HealthDashboardSnapshot) -> None:
        metrics = dict(snapshot.metrics)
        feed = metrics.get("Market Data", "--")
        broker = metrics.get("Broker", "--")
        ai = metrics.get("AI", "--")
        feed = "UNKNOWN" if feed == "--" else feed
        broker = "UNKNOWN" if broker == "--" else broker
        ai = "UNKNOWN" if ai == "--" else ai
        self.data_feed.set_status(
            f"Feed {feed.title()}",
            _level(feed),
        )
        self.broker.set_status(
            f"Broker {broker.title()}",
            _level(broker),
        )
        self.ai.set_status(
            f"AI {ai.title()}",
            _level(ai),
        )
        self._capability_values = dict(snapshot.capabilities)
        self._session_values = dict(snapshot.sessions)
        self._render_capabilities()

    def _render_capabilities(self) -> None:
        capability_values = self._capability_values
        session_values = self._session_values
        research_status = _crypto_research_status(self._crypto_research_source)
        concise = (
            ("Stocks", capability_values.get("Stocks", "Unknown")),
            ("Crypto Research", _research_availability(research_status)),
            # Preserve the broker asset flag as the execution boundary. Never
            # infer it from the independent, read-only research sidecar.
            ("Crypto Trading", capability_values.get("Crypto", "Unknown")),
            ("Options", capability_values.get("Options", "Unknown")),
            ("Overnight", session_values.get("Overnight", "Unknown")),
        )
        self.capabilities.setText(
            "Capabilities: "
            + "  ".join(
                f"{name} {_capability_indicator(value)}"
                for name, value in concise
            )
        )
        self.capabilities.setToolTip(
            "Crypto Research: "
            f"{research_status.value.replace('_', ' ')}; "
            "Crypto Trading: broker execution capability"
        )


def _level(value: str) -> str:
    normalized = value.upper()
    if normalized in {"CONNECTED", "READY", "RUNNING", "HEALTHY"}:
        return "good"
    if normalized in {"FAILED", "ERROR", "DISCONNECTED", "UNAVAILABLE"}:
        return "danger"
    if normalized in {"DEGRADED", "STARTING", "RECONNECTING"}:
        return "warn"
    return "neutral"


def _capability_indicator(value: str) -> str:
    if value == "Available":
        return "✓"
    if value == "Unknown":
        return "?"
    return "✗"


def _crypto_research_status(source) -> CryptoResearchStatus:
    status_getter = getattr(source, "status_snapshot", None)
    if not callable(status_getter):
        return CryptoResearchStatus.DISABLED
    status = getattr(status_getter(), "status", None)
    return (
        status
        if isinstance(status, CryptoResearchStatus)
        else CryptoResearchStatus.DISABLED
    )


def _research_availability(status: CryptoResearchStatus) -> str:
    if status in {
        CryptoResearchStatus.DISCOVERING,
        CryptoResearchStatus.ACTIVE,
        CryptoResearchStatus.PARTIAL_DATA,
        CryptoResearchStatus.AWAITING_DATA,
    }:
        return "Available"
    return "Unavailable"


__all__ = ["GlobalStatusBar"]

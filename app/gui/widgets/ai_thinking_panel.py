from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.gui.models import AIThinkingSnapshot
from app.gui.widgets.common import StatusIndicator


class AIThinkingPanel(QWidget):
    """Show explicit AI operational state and real projected reasoning."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.state = StatusIndicator("Waiting for next scan")
        self.detail = QLabel("No runtime decision reasoning is available.")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        reasoning_title = QLabel("REASONING")
        reasoning_title.setObjectName("metricTitle")
        self.reasoning = QLabel("Unknown")
        self.reasoning.setWordWrap(True)
        self.last_decision = QLabel("Last decision · Unknown")
        self.last_decision.setObjectName("muted")
        layout.addWidget(self.state)
        layout.addWidget(self.detail)
        layout.addWidget(reasoning_title)
        layout.addWidget(self.reasoning)
        layout.addWidget(self.last_decision)
        layout.addStretch()

    def render(self, snapshot: AIThinkingSnapshot) -> None:
        self.state.set_status(snapshot.state, snapshot.tone)
        self.detail.setText(snapshot.detail)
        self.reasoning.setText(snapshot.reasoning)
        self.last_decision.setText(
            f"Last decision · {snapshot.last_decision}"
        )


__all__ = ["AIThinkingPanel"]

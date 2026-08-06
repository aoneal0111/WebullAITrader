from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from app.gui.models import AIThinkingSnapshot
from app.gui.widgets.common import StatusIndicator


class AIThinkingPanel(QWidget):
    """Show explicit AI operational state and real projected reasoning."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        objective_title = QLabel("CURRENT OBJECTIVE")
        objective_title.setObjectName("metricTitle")
        self.objective = QLabel("Unknown")
        self.objective.setObjectName("aiObjective")
        self.objective.setWordWrap(True)
        state_title = QLabel("OPERATIONAL STATE")
        state_title.setObjectName("metricTitle")
        self.state = StatusIndicator("Waiting for the next scan cycle.")
        self.state.setWordWrap(True)
        reasoning_title = QLabel("REASONING")
        reasoning_title.setObjectName("metricTitle")
        self.reasoning = QLabel("Unknown")
        self.reasoning.setObjectName("aiReasoning")
        self.reasoning.setWordWrap(True)
        facts = QGridLayout()
        facts.setHorizontalSpacing(12)
        facts.setVerticalSpacing(6)
        self.last_decision = _fact(facts, 0, "LAST DECISION")
        self.confidence = _fact(facts, 1, "CONFIDENCE")
        self.next_evaluation = _fact(facts, 2, "NEXT EVALUATION")
        layout.addWidget(objective_title)
        layout.addWidget(self.objective)
        layout.addWidget(state_title)
        layout.addWidget(self.state)
        layout.addWidget(reasoning_title)
        layout.addWidget(self.reasoning)
        layout.addLayout(facts)
        layout.addStretch()

    def render(self, snapshot: AIThinkingSnapshot) -> None:
        self.state.set_status(snapshot.state, snapshot.tone)
        self.objective.setText(snapshot.objective)
        self.reasoning.setText(snapshot.reasoning)
        self.last_decision.setText(snapshot.last_decision)
        self.confidence.setText(snapshot.confidence)
        self.next_evaluation.setText(snapshot.next_evaluation)


def _fact(layout: QGridLayout, row: int, title: str) -> QLabel:
    title_label = QLabel(title)
    title_label.setObjectName("metricTitle")
    value = QLabel("Unknown")
    value.setObjectName("aiFact")
    value.setWordWrap(True)
    layout.addWidget(title_label, row, 0)
    layout.addWidget(value, row, 1)
    layout.setColumnStretch(1, 1)
    return value


__all__ = ["AIThinkingPanel"]

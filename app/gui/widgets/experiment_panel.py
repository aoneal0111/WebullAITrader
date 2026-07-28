from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.backtesting.models import ExperimentSnapshot


class ExperimentPanel(QWidget):
    start_requested = Signal(str, str, str)
    pause_requested = Signal()
    resume_requested = Signal()
    step_requested = Signal()
    stop_requested = Signal()
    compare_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        configuration = QFormLayout()
        self.experiment_id = QLineEdit()
        self.experiment_id.setPlaceholderText("experiment-id")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Experiment name")
        self.strategy_version = QLineEdit("1.0")
        configuration.addRow("Experiment ID", self.experiment_id)
        configuration.addRow("Name", self.name)
        configuration.addRow("Strategy Version", self.strategy_version)
        root.addLayout(configuration)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Start")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.step_button = QPushButton("Step")
        self.stop_button = QPushButton("Stop")
        for button in (
            self.start_button,
            self.pause_button,
            self.resume_button,
            self.step_button,
            self.stop_button,
        ):
            controls.addWidget(button)
        root.addLayout(controls)

        results = QFormLayout()
        self.result_values = {
            value: QLabel("--")
            for value in (
                "Playback Status", "Current Event", "Processed",
                "Speed", "Experiment", "Strategy", "Result Status",
                "Total Trades", "Net PnL", "Win Rate", "Max Drawdown",
            )
        }
        for name, label in self.result_values.items():
            results.addRow(name, label)
        root.addLayout(results)

        comparison = QHBoxLayout()
        self.baseline = QComboBox()
        self.candidate = QComboBox()
        self.compare_button = QPushButton("Compare")
        comparison.addWidget(QLabel("Baseline"))
        comparison.addWidget(self.baseline)
        comparison.addWidget(QLabel("Candidate"))
        comparison.addWidget(self.candidate)
        comparison.addWidget(self.compare_button)
        root.addLayout(comparison)
        self.comparison_result = QLabel("No comparison selected")
        self.comparison_result.setObjectName("muted")
        root.addWidget(self.comparison_result)

        self.start_button.clicked.connect(self._start)
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(self.resume_requested)
        self.step_button.clicked.connect(self.step_requested)
        self.stop_button.clicked.connect(self.stop_requested)
        self.compare_button.clicked.connect(self._compare)

    def render(self, snapshot: ExperimentSnapshot) -> None:
        if not isinstance(snapshot, ExperimentSnapshot):
            raise TypeError("snapshot must be ExperimentSnapshot")
        playback = snapshot.playback
        values = {
            "Playback Status": playback.status.value,
            "Current Event": (
                "--" if playback.current_timestamp is None
                else playback.current_timestamp.isoformat()
            ),
            "Processed": f"{playback.position} / {playback.event_count}",
            "Speed": f"{playback.speed}x",
            "Experiment": "--",
            "Strategy": "--",
            "Result Status": "--",
            "Total Trades": "0",
            "Net PnL": "$0.00",
            "Win Rate": "0.00%",
            "Max Drawdown": "$0.00",
        }
        selected = next(
            (
                result
                for result in snapshot.experiments
                if result.experiment.experiment_id
                == snapshot.selected_experiment_id
            ),
            None,
        )
        if selected is not None:
            performance = selected.analytics.performance
            values.update(
                {
                    "Experiment": selected.experiment.name,
                    "Strategy": selected.experiment.configuration.strategy_version,
                    "Result Status": selected.playback_status.value,
                    "Total Trades": str(performance.total_trades),
                    "Net PnL": _money(performance.net_realized_pnl),
                    "Win Rate": _percent(performance.win_rate),
                    "Max Drawdown": _money(
                        selected.analytics.risk.maximum_drawdown
                    ),
                }
            )
        for name, value in values.items():
            self.result_values[name].setText(value)
        identifiers = tuple(
            result.experiment.experiment_id
            for result in snapshot.experiments
        )
        self._choices(self.baseline, identifiers)
        self._choices(self.candidate, identifiers)
        comparison = snapshot.comparison
        if comparison.metrics:
            deltas = {
                metric.name: metric.delta
                for metric in comparison.metrics
            }
            self.comparison_result.setText(
                f"PnL Δ {deltas.get('net_realized_pnl')} · "
                f"Win Rate Δ {deltas.get('win_rate')} · "
                f"Drawdown Δ {deltas.get('maximum_drawdown')} · "
                f"Expectancy Δ {deltas.get('expectancy')}"
            )
        else:
            self.comparison_result.setText("No comparison selected")

    def _start(self) -> None:
        self.start_requested.emit(
            self.experiment_id.text().strip(),
            self.name.text().strip(),
            self.strategy_version.text().strip(),
        )

    def _compare(self) -> None:
        baseline = self.baseline.currentData()
        candidate = self.candidate.currentData()
        if isinstance(baseline, str) and isinstance(candidate, str):
            self.compare_requested.emit(baseline, candidate)

    @staticmethod
    def _choices(combo: QComboBox, values: tuple[str, ...]) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for value in values:
            combo.addItem(value, value)
        if current in values:
            combo.setCurrentIndex(values.index(current))
        combo.blockSignals(False)


def _money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.2f}%"

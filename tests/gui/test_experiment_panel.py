from datetime import datetime, timezone

from app.backtesting.models import ExperimentSnapshot
from app.gui.widgets.experiment_panel import ExperimentPanel


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def test_experiment_panel_renders_snapshot_and_emits_user_intent(qtbot) -> None:
    panel = ExperimentPanel()
    qtbot.addWidget(panel)
    starts = []
    comparisons = []
    panel.start_requested.connect(
        lambda *values: starts.append(values)
    )
    panel.compare_requested.connect(
        lambda *values: comparisons.append(values)
    )
    panel.render(ExperimentSnapshot.initial())
    panel.experiment_id.setText("one")
    panel.name.setText("One")
    panel.strategy_version.setText("v1")
    panel._start()

    assert starts == [("one", "One", "v1")]
    assert panel.result_values["Playback Status"].text() == "EMPTY"
    assert panel.result_values["Processed"].text() == "0 / 0"
    assert panel.comparison_result.text() == "No comparison selected"

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.composition import create_desktop_composition
from app.gui.main_window import MainWindow
from app.operations_core import RuntimeStarted, RuntimeStarting
from app.replay import ReplayEventArchive


APPLICATION = QApplication.instance() or QApplication([])
NOW = datetime(2026, 7, 28, 19, 0, tzinfo=timezone.utc)


def test_atlas_automatically_switches_to_replay_projection() -> None:
    composition = create_desktop_composition()
    window = MainWindow(
        composition.bus,
        composition.state_store,
        composition.runtime_service,
        composition.decision_projector,
        composition.runtime_health_projector,
        composition.timeline_projector,
        composition.trade_lifecycle_projector,
        composition.operator_workspace_projector,
        composition.replay_controller,
        composition.replay_projections,
        composition.recording_controller,
        composition.event_store_controller,
        composition.analytics_controller,
        composition.backtesting_controller,
    )
    archive = ReplayEventArchive.from_events(
        (
            RuntimeStarting(occurred_at=NOW),
            RuntimeStarted(
                active_model="replayed-model",
                occurred_at=NOW + timedelta(seconds=1),
            ),
        )
    )
    try:
        composition.replay_controller.load(
            archive,
            session_id="atlas-replay",
        )
        composition.replay_controller.seek(2)
        APPLICATION.processEvents()

        assert (
            window.dashboard.replay_panel.mode.text()
            == "REPLAY · COMPLETED"
        )
        assert (
            window.dashboard.replay_panel.session.text()
            == "atlas-replay"
        )
        assert window.dashboard.runtime_ribbon.model._value.text() == (
            "replayed-model"
        )
        assert composition.state_store.snapshot().revision == 0
    finally:
        window.close()
        composition.close(timeout_seconds=1.0)

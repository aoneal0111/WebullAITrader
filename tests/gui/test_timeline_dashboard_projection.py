from datetime import datetime, timezone

import pytest

from app.gui.models import TimelineSnapshot as GuiTimelineSnapshot
from app.gui.projections.dashboard_projection import project_dashboard
from app.operations_core import ApplicationState
from app.read_models.timeline import (
    TimelineCategory,
    TimelineEntry,
    TimelineSeverity,
    TimelineSnapshot as ReadModelTimelineSnapshot,
)


NOW = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc)


def test_dashboard_projects_immutable_timeline_rows_newest_first() -> None:
    read_model = ReadModelTimelineSnapshot(
        entries=(
            TimelineEntry(
                timestamp=NOW,
                category=TimelineCategory.DECISION,
                severity=TimelineSeverity.SUCCESS,
                title="Decision updated",
                description="AAPL entry approved.",
                cycle=4,
                symbol="AAPL",
            ),
            TimelineEntry(
                timestamp=NOW,
                category=TimelineCategory.SYSTEM,
                severity=TimelineSeverity.INFO,
                title="Runtime started",
                description="Paper runtime started.",
            ),
        ),
        max_entries=25,
    )

    timeline = project_dashboard(
        ApplicationState(),
        timeline=read_model,
    ).timeline

    assert timeline.max_entries == 25
    assert timeline.rows[0].category == "DECISION"
    assert timeline.rows[0].severity == "SUCCESS"
    assert timeline.rows[0].summary == (
        "Decision updated: AAPL entry approved. (Cycle 4 | AAPL)"
    )
    assert timeline.rows[1].summary.startswith("Runtime started:")


def test_dashboard_defaults_to_empty_timeline() -> None:
    assert project_dashboard(ApplicationState()).timeline == (
        GuiTimelineSnapshot.initial()
    )


def test_dashboard_rejects_wrong_timeline_snapshot() -> None:
    with pytest.raises(TypeError, match="TimelineReadModelSnapshot"):
        project_dashboard(
            ApplicationState(),
            timeline=object(),  # type: ignore[arg-type]
        )

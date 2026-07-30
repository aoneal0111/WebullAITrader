from datetime import UTC, datetime

from app.gui.models import ActivitySnapshot, TimelineFilter
from app.gui.presenters import TimelinePresenter
from app.operations_core import ApplicationState, OperationsTimelineEntry
from app.read_models.timeline import project_operational_timeline


NOW = datetime(2026, 7, 30, 16, 0, tzinfo=UTC)


class ActivityPanelSpy:
    def __init__(self) -> None:
        self.snapshots = []

    def render(self, snapshot) -> None:
        self.snapshots.append(snapshot)


def test_timeline_presenter_prepares_immutable_activity_view_model() -> None:
    panel = ActivityPanelSpy()
    projection = project_operational_timeline(
        (
            OperationsTimelineEntry(
                timestamp=NOW,
                category="ORDER",
                severity="SUCCESS",
                source="paper-runtime",
                title="Order accepted",
                description="Order order-1 was accepted.",
                related_symbol="AAPL",
                related_order_id="order-1",
            ),
        )
    )

    TimelinePresenter(panel).render(  # type: ignore[arg-type]
        ApplicationState(timeline_projection=projection)
    )

    snapshot = panel.snapshots[0]
    assert isinstance(snapshot, ActivitySnapshot)
    assert isinstance(snapshot.entries, tuple)
    assert snapshot.entries[0].message == (
        "Order accepted: Order order-1 was accepted."
    )
    assert snapshot.entries[0].category == "ORDER"
    assert snapshot.entries[0].severity == "SUCCESS"
    assert snapshot.entries[0].related_symbol == "AAPL"
    assert snapshot.entries[0].related_order_id == "order-1"


def test_timeline_presenter_applies_structured_filters_and_search() -> None:
    panel = ActivityPanelSpy()
    presenter = TimelinePresenter(panel)
    projection = project_operational_timeline(
        (
            OperationsTimelineEntry(
                timestamp=NOW,
                category="ORDER",
                severity="SUCCESS",
                source="paper-runtime",
                title="Order accepted",
                description="AAPL order accepted.",
                related_symbol="AAPL",
            ),
            OperationsTimelineEntry(
                timestamp=NOW.replace(hour=15, minute=59),
                category="SYSTEM",
                severity="WARNING",
                source="risk-runtime",
                title="Risk warning",
                description="MSFT exposure elevated.",
                related_symbol="MSFT",
            ),
        )
    )
    state = ApplicationState(timeline_projection=projection)
    presenter.render(state)

    presenter.set_filters(
        TimelineFilter(
            severity="WARNING",
            category="SYSTEM",
            symbol="MSFT",
            search="exposure",
        )
    )

    snapshot = panel.snapshots[-1]
    assert len(snapshot.entries) == 1
    assert snapshot.entries[0].related_symbol == "MSFT"
    assert snapshot.filters.search == "exposure"
    assert snapshot.severity_options == ("ALL", "SUCCESS", "WARNING")
    assert snapshot.category_options == ("ALL", "ORDER", "SYSTEM")
    assert snapshot.symbol_options == ("ALL", "AAPL", "MSFT")

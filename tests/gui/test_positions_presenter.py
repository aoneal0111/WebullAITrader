from datetime import UTC, datetime

from app.gui.models import PositionsSnapshot
from app.gui.presenters import PositionsPresenter
from app.operations_core import ApplicationState, OperationsPosition
from app.read_models.positions.projector import project_operational_positions


NOW = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)


class PositionsPanelSpy:
    def __init__(self) -> None:
        self.snapshots = []

    def render(self, snapshot) -> None:
        self.snapshots.append(snapshot)


def test_positions_presenter_prepares_immutable_view_model() -> None:
    panel = PositionsPanelSpy()
    projection = project_operational_positions(
        (
            OperationsPosition(
                account_id="paper-1",
                symbol="AAPL",
                asset_type="EQUITY",
                quantity="10",
                average_cost="100",
                market_value="1100",
                unrealized_gain_loss="100",
                realized_gain_loss="0",
                currency="USD",
                updated_at=NOW,
            ),
        )
    )
    state = ApplicationState(position_projection=projection)

    PositionsPresenter(panel).render(state)  # type: ignore[arg-type]

    assert panel.snapshots == [
        PositionsSnapshot(
            rows=(("AAPL", "10", "$100.00", "+$100.00"),)
        )
    ]
    assert isinstance(panel.snapshots[0].rows, tuple)


def test_positions_presenter_formats_unknown_unrealized_pnl() -> None:
    panel = PositionsPanelSpy()
    projection = project_operational_positions(
        (
            OperationsPosition(
                account_id="paper-1",
                symbol="AAPL",
                asset_type="EQUITY",
                quantity="10",
                average_cost="100",
                market_value=None,
                unrealized_gain_loss=None,
                realized_gain_loss="0",
                currency="USD",
                updated_at=NOW,
            ),
        )
    )

    PositionsPresenter(panel).render(  # type: ignore[arg-type]
        ApplicationState(position_projection=projection)
    )

    assert panel.snapshots[0].rows[0][3] == "--"

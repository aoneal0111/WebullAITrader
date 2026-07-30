from app.gui.models import PortfolioDashboardSnapshot
from app.gui.presenters import PortfolioPresenter
from app.operations_core import ApplicationState
from app.read_models.portfolio import PortfolioHighlight, PortfolioSummary


class View:
    def __init__(self) -> None:
        self.snapshot = None

    def render(self, snapshot: PortfolioDashboardSnapshot) -> None:
        self.snapshot = snapshot


def test_portfolio_presenter_prepares_immutable_dashboard_model() -> None:
    view = View()
    presenter = PortfolioPresenter(view)
    state = ApplicationState(
        portfolio_projection=PortfolioSummary(
            total_market_value="1200",
            total_cost_basis="1000",
            realized_pnl="50",
            unrealized_pnl="200",
            total_pnl="250",
            gross_exposure="1200",
            long_exposure="1200",
            short_exposure="0",
            open_positions=1,
            working_orders=2,
            winning_positions=1,
            losing_positions=0,
            largest_position=PortfolioHighlight("AAPL", "1200"),
            largest_unrealized_gain=PortfolioHighlight("AAPL", "200"),
            largest_unrealized_loss=None,
        )
    )

    presenter.render(state)

    assert ("Total P/L", "+$250.00") in view.snapshot.metrics
    assert ("Positions / Orders", "1 / 2") in view.snapshot.metrics
    assert (
        "Largest Position",
        "AAPL $1,200.00",
    ) in view.snapshot.highlights

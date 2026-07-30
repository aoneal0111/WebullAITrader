from app.gui.models import PortfolioDashboardSnapshot
from app.gui.presenters import PortfolioPresenter
from datetime import UTC, datetime
from decimal import Decimal

from app.operations_core import ApplicationState, PaperRuntimeSnapshot
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
        paper_runtime=PaperRuntimeSnapshot(
            cycle=1,
            timestamp=datetime(2026, 7, 30, tzinfo=UTC),
            session_id="session-1",
            symbols=("AAPL",),
            decisions_processed=1,
            orders_attempted=1,
            orders_filled=1,
            orders_rejected=0,
            orders_not_filled=0,
            decisions_skipped=0,
            winning_fills=1,
            losing_fills=0,
            breakeven_fills=0,
            realized_pnl=Decimal("50"),
            unrealized_pnl=Decimal("200"),
            current_equity=Decimal("10250"),
            peak_equity=Decimal("10250"),
            current_drawdown=Decimal("0"),
            win_rate=Decimal("1"),
            total_return=Decimal("0.025"),
            maximum_drawdown=Decimal("0"),
        ),
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
    assert ("Equity", "$10,250.00") in view.snapshot.metrics
    assert ("Buying Power", "--") in view.snapshot.metrics
    assert ("Gross Exposure", "$1,200.00") in view.snapshot.metrics
    assert ("Working Orders", "2") in view.snapshot.metrics
    assert ("Positions / Orders", "1 / 2") in view.snapshot.metrics
    assert (
        "Largest Position",
        "AAPL $1,200.00",
    ) in view.snapshot.highlights

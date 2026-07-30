from datetime import UTC, datetime

from app.gui.models import WatchlistSnapshot
from app.gui.presenters import WatchlistPresenter
from app.operations_core import ApplicationState
from app.read_models.watchlist import WatchlistEntry, WatchlistState


class View:
    def __init__(self) -> None:
        self.snapshot = None

    def render(self, snapshot: WatchlistSnapshot) -> None:
        self.snapshot = snapshot


def test_watchlist_presenter_prepares_immutable_ui_model() -> None:
    view = View()
    presenter = WatchlistPresenter(view)
    state = ApplicationState(
        watchlist_projection=WatchlistState(
            ordered_symbols=("AAPL",),
            entries=(
                WatchlistEntry(
                    symbol="AAPL",
                    latest_price="101.25",
                    change="1.25",
                    change_percent="1.25",
                    bid="101.20",
                    ask="101.30",
                    volume=100_000,
                    market_status="OPEN",
                    last_update=datetime(
                        2026,
                        7,
                        30,
                        15,
                        0,
                        tzinfo=UTC,
                    ),
                    stale=False,
                ),
            ),
            selected_symbol="AAPL",
        )
    )

    presenter.render(state)

    row = view.snapshot.rows[0]
    assert row.symbol == "AAPL"
    assert row.selected is True
    assert row.latest_price == "101.25"
    assert row.change == "+1.25"
    assert row.change_percent == "+1.25%"
    assert row.volume == "100,000"
    assert row.stale == "LIVE"

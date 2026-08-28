from datetime import UTC, datetime

from app.gui.formatters.watchlist import format_watchlist
from app.gui.models import WatchlistSnapshot
from app.gui.presenters import WatchlistPresenter
from app.operations_core import ApplicationState
from app.read_models.watchlist import WatchlistEntry, WatchlistState
from app.read_models.health import HealthState


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


def test_watchlist_presenter_sorts_raw_projected_values() -> None:
    view = View()
    presenter = WatchlistPresenter(view)
    state = ApplicationState(
        watchlist_projection=WatchlistState(
            ordered_symbols=("AAPL", "MSFT"),
            entries=(
                WatchlistEntry(
                    symbol="AAPL",
                    latest_price="101.25",
                    change_percent="1.25",
                    volume=100,
                    market_status="OPEN",
                    stale=False,
                ),
                WatchlistEntry(
                    symbol="MSFT",
                    latest_price="450.00",
                    change_percent="-0.50",
                    volume=200,
                    market_status="OPEN",
                    stale=True,
                ),
            ),
            selected_symbol="MSFT",
        )
    )
    presenter.render(state)

    presenter.sort_by("latest_price")
    assert tuple(row.symbol for row in view.snapshot.rows) == (
        "AAPL",
        "MSFT",
    )
    presenter.sort_by("latest_price")

    assert tuple(row.symbol for row in view.snapshot.rows) == (
        "MSFT",
        "AAPL",
    )
    assert view.snapshot.rows[0].selected is True
    assert view.snapshot.rows[0].stale == "STALE"
    assert view.snapshot.rows[0].market_status == "OPEN"


def test_watchlist_presenter_projects_scanner_classification() -> None:
    view = View()
    presenter = WatchlistPresenter(view)
    state = ApplicationState(
        watchlist_projection=WatchlistState(
            ordered_symbols=("QUAL", "WATCH"),
            entries=(
                WatchlistEntry(
                    symbol="QUAL",
                    metadata=(
                        ("scanner_rank", "1"),
                        ("scanner_classification", "QUALIFYING"),
                    ),
                ),
                WatchlistEntry(
                    symbol="WATCH",
                    metadata=(
                        ("scanner_rank", "2"),
                        ("scanner_classification", "WATCHING"),
                    ),
                ),
            ),
        )
    )

    presenter.render(state)

    assert tuple(
        (row.symbol, row.classification)
        for row in view.snapshot.rows
    ) == (
        ("QUAL", "QUALIFYING"),
        ("WATCH", "WATCHING"),
    )


def test_watchlist_presenter_exposes_independent_entry_data_freshness() -> None:
    def entry(
        symbol: str, metadata: tuple[tuple[str, str], ...],
    ) -> WatchlistEntry:
        return WatchlistEntry(symbol=symbol, metadata=metadata)

    state = WatchlistState(
        ordered_symbols=("OLDQUOTE", "OLDLAST", "FRESH"),
        entries=(
            entry("OLDQUOTE", (
                ("scanner_freshness", "STALE"),
                ("scanner_market_age_ms", "57100"),
                ("scanner_last_price_freshness", "LIVE"),
                ("scanner_last_price_age_ms", "100"),
                ("scanner_quote_freshness", "STALE"),
                ("scanner_quote_age_ms", "57100"),
            )),
            entry("OLDLAST", (
                ("scanner_freshness", "STALE"),
                ("scanner_market_age_ms", "30200"),
                ("scanner_last_price_freshness", "STALE"),
                ("scanner_last_price_age_ms", "30200"),
                ("scanner_quote_freshness", "LIVE"),
                ("scanner_quote_age_ms", "200"),
            )),
            entry("FRESH", (
                ("scanner_freshness", "LIVE"),
                ("scanner_market_age_ms", "300"),
                ("scanner_last_price_freshness", "LIVE"),
                ("scanner_last_price_age_ms", "300"),
                ("scanner_quote_freshness", "LIVE"),
                ("scanner_quote_age_ms", "200"),
            )),
        ),
    )

    rows = {row.symbol: row for row in format_watchlist(state).rows}

    assert rows["OLDQUOTE"].freshness == (
        "LAST LIVE | QUOTE 57.1s | ENTRY DATA STALE"
    )
    assert rows["OLDLAST"].freshness == (
        "LAST 30.2s | QUOTE LIVE | ENTRY DATA STALE"
    )
    assert rows["FRESH"].freshness == (
        "LAST LIVE | QUOTE LIVE | ENTRY DATA LIVE"
    )


def test_empty_atlas_focus_explains_overnight_capability_pause() -> None:
    view = View()
    presenter = WatchlistPresenter(view)

    presenter.render(ApplicationState(
        health_projection=HealthState(
            entitlement_status="NOT_SUBSCRIBED",
            scanner_status="PAUSED_UNTIL_PREMARKET",
        )
    ))

    assert view.snapshot.empty_title == "AI Scanner paused."
    assert "Reason:" in view.snapshot.empty_detail
    assert "Overnight subscription required." in (
        view.snapshot.empty_detail
    )
    assert "resume automatically" in view.snapshot.empty_detail

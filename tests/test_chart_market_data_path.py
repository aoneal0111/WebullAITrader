from datetime import UTC, datetime
import logging
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from app.gui.presenters.chart_presenter import ChartPresenter
from app.gui.projections.chart_projection import ChartProjection
from app.services.chart_market_data import ChartMarketDataService
from app.operations_core import ApplicationState, OperationsOrder, OperationsPosition
from app.read_models.watchlist import WatchlistEntry, WatchlistState
from app.read_models.health import HealthState
from app.webull.sdk_market_data import LazyOfficialDataClient


class Response:
    def __init__(self, data):
        self._data = data

    def json(self):
        return {"data": self._data}


class MarketData:
    def __init__(self):
        self.calls = []

    def get_snapshot(self, *, symbols, category):
        self.calls.append(("snapshot", symbols, category))
        return Response([{"symbol": symbols[0], "price": "103"}])

    def get_quotes(self, symbol, category):
        self.calls.append(("quote", symbol, category))
        return Response([{
            "symbol": symbol,
            "open": "100",
            "high": "104",
            "low": "99",
            "price": "103",
        }])

    def get_history_bar(
        self, symbol, category, timespan, *, count, real_time_required
    ):
        self.calls.append((
            "bars", symbol, category, timespan, count, real_time_required
        ))
        return Response([{
            "symbol": symbol,
            "bars": [
                {
                    "timestamp": 1786035600000,
                    "open": "100",
                    "high": "104",
                    "low": "99",
                    "close": "103",
                    "volume": "1200",
                }
            ],
        }])


def test_chart_projection_requests_snapshot_quote_and_bars_through_rest(caplog):
    market_data = MarketData()
    service = ChartMarketDataService(
        LazyOfficialDataClient(
            lambda: SimpleNamespace(market_data=market_data)
        )
    )

    with caplog.at_level(logging.INFO):
        model = ChartProjection(service).request("aapl")

    assert market_data.calls == [
        ("snapshot", ["AAPL"], "US_STOCK"),
        ("quote", "AAPL", "US_STOCK"),
        ("bars", "AAPL", "US_STOCK", "D", "120", False),
    ]
    assert model.symbol == "AAPL"
    assert model.open == 100
    assert model.close == 103
    assert len(model.candles) == 1
    assert model.candles[0].timestamp == datetime(
        2026, 8, 6, 17, 0, tzinfo=UTC
    )
    assert "operation=snapshot_request status=started symbol=AAPL" in caplog.text
    assert "operation=quote_request status=started symbol=AAPL" in caplog.text
    assert "operation=historical_bar_request status=started symbol=AAPL" in caplog.text
    assert "operation=chart_model_update symbol=AAPL candle_count=1" in caplog.text


def test_chart_service_logs_all_request_skip_reasons_without_symbol(caplog):
    service = ChartMarketDataService(
        LazyOfficialDataClient(
            lambda: (_ for _ in ()).throw(AssertionError("must stay lazy"))
        )
    )

    with caplog.at_level(logging.INFO):
        result = service.load("")

    assert result.bars == ()
    for operation in (
        "snapshot_request",
        "quote_request",
        "historical_bar_request",
    ):
        assert (
            f"operation={operation} status=skipped symbol=-- "
            "reason=no selected symbol"
        ) in caplog.text


class ChartView(QObject):
    chart_symbol_selected = Signal(str)
    chart_timeframe_selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.models = []
        self.managed = False

    def set_chart_managed(self, managed):
        self.managed = managed

    def render_chart(self, model):
        self.models.append(model)


def test_chart_presenter_does_not_use_configured_symbol_without_active_source(caplog):
    market_data = MarketData()
    service = ChartMarketDataService(LazyOfficialDataClient(
        lambda: SimpleNamespace(market_data=market_data)
    ))
    view = ChartView()
    presenter = ChartPresenter(
        view,
        ChartProjection(service),
        default_symbol="aapl",
        asynchronous=False,
    )

    with caplog.at_level(logging.INFO):
        presenter.render(ApplicationState())

    assert view.managed is True
    assert view.models[-1].symbol == "--"
    assert "idle" in view.models[-1].message.lower()
    assert market_data.calls == []
    assert "symbol=-- reason=no active instrument source" in caplog.text
    presenter.close()


def test_successful_rest_bars_publish_authoritative_observation():
    observations = []
    service = ChartMarketDataService(
        LazyOfficialDataClient(
            lambda: SimpleNamespace(market_data=MarketData())
        ),
        observation_sink=lambda *values: observations.append(values),
    )

    result = service.load("AAPL")

    assert result.bars
    assert observations == [("HISTORICAL_BARS_LOADED", "AAPL", 1)]


def test_explicit_operator_chart_selection_wins_over_scanner_projection():
    market_data = MarketData()
    view = ChartView()
    presenter = ChartPresenter(
        view,
        ChartProjection(ChartMarketDataService(LazyOfficialDataClient(
            lambda: SimpleNamespace(market_data=market_data)
        ))),
        default_symbol="AAPL",
        asynchronous=False,
    )
    presenter.select_symbol("MSFT")
    presenter.render(ApplicationState(watchlist_projection=WatchlistState(
        ordered_symbols=("NVDA",),
        entries=(WatchlistEntry(symbol="NVDA"),),
        selected_symbol="NVDA",
    )))

    assert view.models[-1].symbol == "MSFT"
    assert view.models[-1].selection_source == "operator inspection"
    assert not any(call[1] == "NVDA" for call in market_data.calls if len(call) > 1)
    presenter.close()


def _scanner_watchlist(symbol: str) -> WatchlistState:
    return WatchlistState(
        ordered_symbols=(symbol,),
        entries=(WatchlistEntry(
            symbol=symbol,
            metadata=(("scanner_rank", "1"),),
        ),),
        selected_symbol=symbol,
    )


def test_operator_inspection_survives_candidate_exit_and_new_focus_until_clear(
    caplog,
):
    market_data = MarketData()
    view = ChartView()
    presenter = ChartPresenter(
        view,
        ChartProjection(ChartMarketDataService(LazyOfficialDataClient(
            lambda: SimpleNamespace(market_data=market_data)
        ))),
        asynchronous=False,
    )

    presenter.render(ApplicationState(
        watchlist_projection=_scanner_watchlist("WYHG")
    ))
    assert view.models[-1].symbol == "WYHG"
    assert view.models[-1].selection_source == "atlas candidate"
    assert len(view.models[-1].candles) == 1

    with caplog.at_level(logging.INFO):
        presenter.select_symbol("WYHG")
        presenter.render(ApplicationState(health_projection=HealthState(
            last_market_data_event=datetime(2026, 8, 10, 15, 0, tzinfo=UTC),
            subscription_symbols=("WYHG",),
        )))

    assert view.models[-1].symbol == "WYHG"
    assert view.models[-1].selection_source == "operator inspection"
    assert len(view.models[-1].candles) == 1
    assert view.models[-1].historical_data_available is True
    assert view.models[-1].last_stream_update == datetime(
        2026, 8, 10, 15, 0, tzinfo=UTC
    )
    assert "source=operator inspection" in caplog.text

    presenter.render(ApplicationState(health_projection=HealthState(
        last_market_data_event=datetime(2026, 8, 10, 15, 0, tzinfo=UTC),
        subscription_symbols=(),
    )))
    assert view.models[-1].symbol == "WYHG"
    assert view.models[-1].last_stream_update is None
    assert len(view.models[-1].candles) == 1

    calls_before_new_candidate = tuple(market_data.calls)
    presenter.render(ApplicationState(
        watchlist_projection=_scanner_watchlist("NVDA")
    ))
    assert view.models[-1].symbol == "WYHG"
    assert tuple(market_data.calls) == calls_before_new_candidate

    presenter.select_symbol("NVDA")
    assert view.models[-1].symbol == "NVDA"
    assert view.models[-1].selection_source == "operator inspection"

    presenter.clear_inspection()
    assert view.models[-1].symbol == "NVDA"
    assert view.models[-1].selection_source == "atlas candidate"
    presenter.close()


def test_bootstrap_symbol_never_becomes_a_visible_neutral_fallback():
    view = ChartView()
    presenter = ChartPresenter(
        view,
        ChartProjection(ChartMarketDataService(LazyOfficialDataClient(
            lambda: SimpleNamespace(market_data=MarketData())
        ))),
        default_symbol="AAPL",
        asynchronous=False,
    )
    presenter.render(ApplicationState())

    assert view.models[-1].symbol == "--"
    assert view.models[-1].selection_source == "none"
    presenter.close()


def test_selected_non_scanner_bootstrap_watchlist_symbol_is_not_atlas_focus():
    view = ChartView()
    presenter = ChartPresenter(
        view,
        ChartProjection(ChartMarketDataService(LazyOfficialDataClient(
            lambda: SimpleNamespace(market_data=MarketData())
        ))),
        default_symbol="AAPL",
        asynchronous=False,
    )
    presenter.render(ApplicationState(watchlist_projection=WatchlistState(
        ordered_symbols=("AAPL",),
        entries=(WatchlistEntry(symbol="AAPL"),),
        selected_symbol="AAPL",
    )))

    assert view.models[-1].symbol == "--"
    presenter.close()


def test_selected_scanner_candidate_is_valid_atlas_focus():
    view = ChartView()
    presenter = ChartPresenter(
        view,
        ChartProjection(ChartMarketDataService(LazyOfficialDataClient(
            lambda: SimpleNamespace(market_data=MarketData())
        ))),
        asynchronous=False,
    )
    presenter.render(ApplicationState(watchlist_projection=WatchlistState(
        ordered_symbols=("NVDA",),
        entries=(WatchlistEntry(
            symbol="NVDA",
            metadata=(("scanner_rank", "1"),),
        ),),
        selected_symbol="NVDA",
    )))

    assert view.models[-1].symbol == "NVDA"
    assert view.models[-1].selection_source == "atlas candidate"
    presenter.close()


@pytest.mark.parametrize(
    ("state", "symbol", "source"),
    (
        (
            ApplicationState(positions=(OperationsPosition(
                account_id="account", symbol="MSFT", asset_type="EQUITY",
                quantity="2", average_cost="100", market_value=None,
                unrealized_gain_loss=None, realized_gain_loss=None,
                currency="USD", updated_at=datetime(2026, 8, 10, tzinfo=UTC),
            ),)),
            "MSFT",
            "active position",
        ),
        (
            ApplicationState(orders=(OperationsOrder(
                order_id="order-1", symbol="NVDA", side="BUY", quantity="1",
                status="WORKING", updated_at=datetime(2026, 8, 10, tzinfo=UTC),
            ),)),
            "NVDA",
            "working order",
        ),
    ),
)
def test_position_and_working_order_are_valid_chart_sources(state, symbol, source):
    view = ChartView()
    presenter = ChartPresenter(
        view,
        ChartProjection(ChartMarketDataService(LazyOfficialDataClient(
            lambda: SimpleNamespace(market_data=MarketData())
        ))),
        default_symbol="AAPL",
        asynchronous=False,
    )

    presenter.render(state)

    assert view.models[-1].symbol == symbol
    assert view.models[-1].selection_source == source
    presenter.close()


@pytest.mark.parametrize(
    ("timeframe", "timespan"),
    (("1m", "M1"), ("5m", "M5"), ("15m", "M15"), ("1H", "H1"), ("1D", "D")),
)
def test_supported_timeframes_request_matching_historical_range(timeframe, timespan):
    market_data = MarketData()
    service = ChartMarketDataService(LazyOfficialDataClient(
        lambda: SimpleNamespace(market_data=market_data)
    ))

    result = service.load("XYZ", timeframe)

    assert result.timeframe == timeframe
    bars_call = next(call for call in market_data.calls if call[0] == "bars")
    assert bars_call[3] == timespan

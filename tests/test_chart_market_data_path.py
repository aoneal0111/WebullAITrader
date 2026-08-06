from datetime import UTC, datetime
import logging
from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal

from app.gui.presenters.chart_presenter import ChartPresenter
from app.gui.projections.chart_projection import ChartProjection
from app.services.chart_market_data import ChartMarketDataService
from app.operations_core import ApplicationState
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


def test_chart_presenter_uses_configured_symbol_without_stream_or_watchlist(caplog):
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
    assert view.models[0].message.startswith("Loading snapshot")
    assert view.models[-1].symbol == "AAPL"
    assert len(view.models[-1].candles) == 1
    assert "source=configured fallback" in caplog.text
    assert [call[0] for call in market_data.calls] == [
        "snapshot", "quote", "bars"
    ]
    presenter.close()

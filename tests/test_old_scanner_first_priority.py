from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.live_scanner import LiveScannerCoordinator
from app.momentum_radar import MomentumRadar
from app.momentum_scanner import AssetClass, CatalystType
from app.realtime_scanner import RealtimeScannerEngine
from app.reference_data import ReferenceRecord
from app.universe import SecurityType, UniverseSelection, UniverseSymbol
from app.universe.models import UniversePriorityLanes
from app.webull.sdk_market_data import (
    LazyOfficialDataClient,
    WebullScannerUniverseProvider,
)


PREMARKET = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
D = Decimal


class _Response:
    status_code = 200

    def __init__(self, rows) -> None:
        self._rows = rows

    def json(self):
        return {"data": self._rows}


def _row(symbol: str, *, price: str = "5", volume: str = "100000"):
    return {
        "symbol": symbol,
        "exchange_code": "NSQ",
        "currency_code": "USD",
        "price": price,
        "pre_close": "4",
        "change_ratio": "0.25",
        "volume": volume,
        "relative_volume_10d": "5",
        "turnover": str(D(price) * D(volume)),
        "high": price,
    }


class _Screener:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.gainers = [_row(f"G{index:03d}") for index in range(50)]
        self.rvol = [_row(f"R{index:03d}") for index in range(50)]
        self.volume = [_row(f"V{index:03d}") for index in range(150)]
        self.turnover = [_row(f"T{index:03d}") for index in range(150)]

    def get_gainers_losers(self, *args, **kwargs):
        self.calls.append(("GAINERS", args, kwargs))
        return _Response(self._page(self.gainers, kwargs))

    def get_most_active(self, *args, **kwargs):
        source = kwargs["sort_by"]
        self.calls.append((source, args, kwargs))
        values = {
            "RELATIVE_VOLUME_10D": self.rvol,
            "VOLUME": self.volume,
            "TURNOVER": self.turnover,
        }[source]
        return _Response(self._page(values, kwargs))

    @staticmethod
    def _page(values, kwargs):
        start = (kwargs["page_index"] - 1) * kwargs["page_size"]
        return values[start:start + kwargs["page_size"]]


def _provider(screener: _Screener, radar: MomentumRadar | None = None):
    return WebullScannerUniverseProvider(
        LazyOfficialDataClient(lambda: SimpleNamespace(screener=screener)),
        clock=lambda: PREMARKET,
        maximum_breadth=250,
        sources=(
            "SESSION_GAINERS",
            "RELATIVE_VOLUME_10D",
            "VOLUME_LEADERS",
            "TURNOVER_LEADERS",
        ),
        legacy_source_limit=50,
        accelerator_capacity=10,
        radar=radar,
    )


def test_legacy_selection_is_first_and_premarket_request_shape_is_preserved() -> None:
    screener = _Screener()
    provider = _provider(screener, MomentumRadar())

    symbols = provider.list_symbols(AssetClass.STOCK)
    lanes = provider.priority_lanes()

    expected_legacy = tuple(
        [f"G{index:03d}" for index in range(50)]
        + [f"R{index:03d}" for index in range(50)]
    )
    assert lanes.legacy_primary == expected_legacy
    assert tuple(item.symbol for item in symbols[:100]) == expected_legacy
    gainer_call = screener.calls[0]
    assert gainer_call[1][:3] == ("PRE_MARKET", "US_STOCK", "CHANGE_RATIO")
    assert gainer_call[2]["direction"] == "DESC"
    assert any(
        source == "RELATIVE_VOLUME_10D" for source, _args, _kwargs in screener.calls
    )
    assert len(lanes.background) >= 290


def test_accelerator_is_additive_after_legacy_and_row_merge_is_nondestructive() -> None:
    screener = _Screener()
    screener.volume = [
        {**_row("ACC", price="5", volume="100000"), "change_ratio": "0.10"},
        _row("EMG", price="5", volume="100000"),
        *screener.volume[2:],
    ]
    # A later broad row must not erase the gainer's stronger usable field.
    screener.gainers[0] = {**_row("ACC"), "change_ratio": "1.70"}
    radar = MomentumRadar()
    provider = _provider(screener, radar)
    provider.list_symbols(AssetClass.STOCK)

    screener.volume[0] = {
        **screener.volume[0], "price": "6", "high": "6", "volume": "300000",
    }
    screener.volume[1] = {
        **screener.volume[1], "price": "6", "high": "6", "volume": "300000",
    }
    symbols = provider.list_symbols(AssetClass.STOCK)
    lanes = provider.priority_lanes()

    assert provider.row_for("ACC")["change_ratio"] == "1.70"
    assert radar.assessment("EMG").state == "MOMENTUM_EMERGING"
    # ACC is already legacy, so acceleration strengthens it without replacing
    # or duplicating the primary lane.
    assert lanes.legacy_primary[0] == "ACC"
    assert tuple(item.symbol for item in symbols).count("ACC") == 1
    assert "EMG" in lanes.accelerator
    assert "EMG" not in lanes.legacy_primary
    assert tuple(item.symbol for item in symbols).index("EMG") >= 100
    coordinator = object.__new__(LiveScannerCoordinator)
    coordinator._engine = SimpleNamespace(subscription_priority_lanes=lanes)
    coordinator._maximum_subscription_channels = 101
    coordinator._retained_channels_source = lambda: ()
    coordinator._accelerator_channels_source = None
    observed = coordinator._effective_channels(
        tuple(item.symbol for item in symbols),
    )
    assert observed[:100] == lanes.legacy_primary
    assert observed[100] == "EMG"
    assert not hasattr(radar, "authorize")


def _symbol(symbol: str) -> UniverseSymbol:
    return UniverseSymbol(
        symbol=symbol,
        asset_class=AssetClass.STOCK,
        exchange="NASDAQ",
        security_type=SecurityType.COMMON_STOCK,
        tradable=True,
        price=D("5"),
        average_30_day_volume=D("1000000"),
        quote_currency="USD",
    )


class _LaneUniverse:
    def __init__(self, symbols, lanes) -> None:
        self._symbols = tuple(symbols)
        self._lanes = lanes

    def select_all(self, _asset_classes=()):
        return UniverseSelection(included=self._symbols, excluded=())

    def priority_lanes(self):
        return self._lanes


class _References:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, symbol, asset_class=AssetClass.STOCK, *, force_refresh=False):
        self.calls.append(symbol)
        return ReferenceRecord(
            symbol=symbol,
            asset_class=asset_class,
            exchange="NASDAQ",
            previous_close=D("4"),
            average_30_day_volume=D("1000000"),
            float_shares=D("8000000"),
            market_cap=None,
            shares_outstanding=None,
            tradable=True,
            catalyst=CatalystType.EARNINGS,
            catalyst_headline="Test",
            as_of=PREMARKET,
        )


class _Pipeline:
    def consume(self, _event):
        return None


def test_reference_first_100_is_retained_then_complete_legacy_lane() -> None:
    legacy = tuple(
        [f"G{index:03d}" for index in range(50)]
        + [f"R{index:03d}" for index in range(50)]
    )
    accelerator = ("ACC",)
    background = tuple(f"B{index:03d}" for index in range(300))
    service = _LaneUniverse(
        tuple(_symbol(symbol) for symbol in (*background, *accelerator, *legacy)),
        UniversePriorityLanes(legacy, accelerator, background),
    )
    references = _References()
    engine = RealtimeScannerEngine(
        service, references, _Pipeline(), maximum_active_symbols=100,
    )
    retained = legacy[-1]
    engine.set_authoritative_symbols_source(lambda: (retained,))

    engine.refresh_universe((AssetClass.STOCK,))

    expected_first_100 = (retained, *(symbol for symbol in legacy if symbol != retained))
    assert tuple(references.calls[:100]) == expected_first_100
    assert references.calls[100] == "ACC"
    assert not any(symbol.startswith("B") for symbol in references.calls)
    assert len(references.calls) == len(set(references.calls)) == 101
    assert engine.reference_refresh_metrics().generation == 1


def test_external_catalyst_accelerator_reaches_production_reference_lane() -> None:
    screener = _Screener()
    provider = _provider(screener, MomentumRadar())
    provider.set_accelerator_symbols_source(lambda: ("V149",))

    symbols = provider.list_symbols(AssetClass.STOCK)

    assert provider.priority_lanes().accelerator[0] == "V149"
    assert "V149" in tuple(item.symbol for item in symbols)
    assert "V149" not in provider.priority_lanes().legacy_primary


def test_subscription_priority_preserves_retained_legacy_and_additive_accelerator() -> None:
    engine = SimpleNamespace(
        subscription_priority_lanes=UniversePriorityLanes(
            legacy_primary=("LATE_Z", "EARLY_A"),
            accelerator=("ACC",),
            background=("AAAA", "BBBB"),
        )
    )
    coordinator = object.__new__(LiveScannerCoordinator)
    coordinator._engine = engine
    coordinator._maximum_subscription_channels = 5
    coordinator._retained_channels_source = lambda: ("MANAGED",)
    coordinator._accelerator_channels_source = lambda: ("CATALYST",)

    selected = coordinator._effective_channels(
        ("AAAA", "BBBB", "ACC", "EARLY_A", "LATE_Z"),
    )

    assert selected == ("MANAGED", "LATE_Z", "EARLY_A", "ACC", "CATALYST")
    assert len(selected) == 5
    assert "AAAA" not in selected

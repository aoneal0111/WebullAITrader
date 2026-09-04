from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.momentum_scanner import AssetClass
from app.realtime_scanner import RealtimeScannerEngine
from app.scanner_universe_observability import UniverseAdmissionStage
from app.universe import SecurityType, UniverseService, UniverseSymbol
from app.webull.sdk_market_data import (
    LazyOfficialDataClient,
    WebullScannerUniverseProvider,
)


NOW = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)


class Response:
    status_code = 200

    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return {"data": self._rows}


def row(symbol: str, *, price="5", volume="2000000", rvol="2"):
    return {
        "symbol": symbol,
        "exchange_code": "NSQ",
        "currency_code": "USD",
        "price": price,
        "volume": volume,
        "relative_volume_10d": rvol,
        "change_ratio": "0.25",
    }


class Screener:
    def __init__(self):
        self.calls = []

    def get_gainers_losers(self, *args, **kwargs):
        self.calls.append(("gainers", args, kwargs))
        return Response([row("DUAL"), row("GAIN"), row("BAD", price="0")])

    def get_most_active(self, *args, **kwargs):
        self.calls.append(("rvol", args, kwargs))
        return Response([row("DUAL"), row("RVOL")])


class RecordingObserver:
    def __init__(self, *, failing=False):
        self.events = []
        self.refreshes = []
        self.failing = failing

    def begin_refresh(self, **values):
        if self.failing:
            raise RuntimeError("research unavailable")
        self.refreshes.append(values)

    def record(self, **values):
        if self.failing:
            raise RuntimeError("research unavailable")
        self.events.append(values)

    def close(self):
        return True


def events(observer, stage):
    return [e for e in observer.events if e["stage"] is stage]


def test_raw_sources_ranks_request_window_dedup_and_normalization_are_recorded():
    screener = Screener()
    observer = RecordingObserver()
    provider = WebullScannerUniverseProvider(
        LazyOfficialDataClient(lambda: SimpleNamespace(screener=screener)),
        clock=lambda: NOW,
        admission_observer=observer,
    )

    actual = provider.list_symbols(AssetClass.STOCK)

    assert [item.symbol for item in actual] == ["DUAL", "GAIN", "RVOL"]
    assert screener.calls[0][2] == {
        "page_index": 1, "page_size": 50, "direction": "DESC"
    }
    assert screener.calls[1][2] == {
        "sort_by": "RELATIVE_VOLUME_10D", "page_index": 1,
        "page_size": 50, "direction": "DESC",
    }
    returned = events(observer, UniverseAdmissionStage.SCREENER_RETURNED)
    assert [(e["screener_identity"], e["source_rank"], e["raw_symbol"])
            for e in returned] == [
        ("DAY_GAINERS", 1, "DUAL"),
        ("DAY_GAINERS", 2, "GAIN"),
        ("DAY_GAINERS", 3, "BAD"),
        ("RELATIVE_VOLUME_10D", 1, "DUAL"),
        ("RELATIVE_VOLUME_10D", 2, "RVOL"),
    ]
    assert len(events(observer, UniverseAdmissionStage.REQUEST_WINDOW_INCLUDED)) == 5
    dedup = events(observer, UniverseAdmissionStage.SOURCE_DEDUPLICATED)
    dual = next(e for e in dedup if e["normalized_symbol"] == "DUAL")
    assert dual["outcome"].value == "MERGED"
    assert dual["upstream_fields"]["sources"] == [
        ("DAY_GAINERS", 1), ("RELATIVE_VOLUME_10D", 1)
    ]
    normalized = events(observer, UniverseAdmissionStage.SYMBOL_NORMALIZED)
    assert {e["normalized_symbol"] for e in normalized} == {"DUAL", "GAIN", "RVOL"}
    rejected = events(observer, UniverseAdmissionStage.NORMALIZATION_REJECTED)
    assert [(e["raw_symbol"], e["reason"]) for e in rejected] == [
        ("BAD", "MISSING_POSITIVE_PRICE")
    ]


def test_observer_failure_does_not_change_provider_or_universe_filter_results():
    lazy = LazyOfficialDataClient(lambda: SimpleNamespace(screener=Screener()))
    baseline = WebullScannerUniverseProvider(lazy, clock=lambda: NOW).list_symbols(
        AssetClass.STOCK
    )
    observed = WebullScannerUniverseProvider(
        lazy, clock=lambda: NOW, admission_observer=RecordingObserver(failing=True)
    ).list_symbols(AssetClass.STOCK)
    assert observed == baseline

    observer = RecordingObserver()
    accepted = UniverseService(
        _StaticProvider(observed), admission_observer=observer
    ).select(AssetClass.STOCK)
    assert {item.symbol for item in accepted.included} == {"DUAL", "GAIN", "RVOL"}
    assert len(events(observer, UniverseAdmissionStage.UNIVERSE_FILTER_ACCEPTED)) == 3


def test_existing_universe_filter_rejection_and_reason_are_observed_without_change():
    observer = RecordingObserver()
    low_volume = UniverseSymbol(
        symbol="THIN", asset_class=AssetClass.STOCK, exchange="NASDAQ",
        security_type=SecurityType.COMMON_STOCK, tradable=True,
        price=Decimal("5"), average_30_day_volume=Decimal("10"),
    )
    selection = UniverseService(
        _StaticProvider((low_volume,)), admission_observer=observer
    ).select(AssetClass.STOCK)
    assert selection.included == ()
    assert selection.excluded == (low_volume,)
    rejected = events(observer, UniverseAdmissionStage.UNIVERSE_FILTER_REJECTED)
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "AVERAGE_VOLUME"


class _StaticProvider:
    def __init__(self, values):
        self.values = values

    def list_symbols(self, asset_class):
        return self.values if asset_class is AssetClass.STOCK else ()


class _ReferenceService:
    def get_for_instrument(self, item, *, force_refresh=False):
        if item.symbol == "BADREF":
            raise LookupError("reference absent")
        return SimpleNamespace(as_of=NOW)


class _Pipeline:
    def __init__(self, decision=None):
        self.events = []
        self.decision = decision

    def consume(self, event):
        self.events.append(event)
        return self.decision


def _symbol(name):
    return UniverseSymbol(
        symbol=name,
        asset_class=AssetClass.STOCK,
        exchange="NASDAQ",
        security_type=SecurityType.COMMON_STOCK,
        tradable=True,
        price=Decimal("5"),
        average_30_day_volume=Decimal("1000000"),
    )


def test_reference_warmup_admission_and_first_scanner_boundary_are_observed_once():
    observer = RecordingObserver()
    observer.begin_refresh(timestamp=NOW, session="REGULAR", page_size=50)
    pipeline = _Pipeline()
    engine = RealtimeScannerEngine(
        SimpleNamespace(select_all=lambda _: SimpleNamespace(
            included=(_symbol("GOOD"), _symbol("BADREF")), excluded=()
        )),
        _ReferenceService(),
        pipeline,
        admission_observer=observer,
        clock=lambda: NOW,
    )

    assert engine.refresh_universe((AssetClass.STOCK,)) == ("GOOD",)
    assert len(events(observer, UniverseAdmissionStage.REFERENCE_WARMUP_STARTED)) == 2
    assert len(events(observer, UniverseAdmissionStage.REFERENCE_WARMUP_ACCEPTED)) == 1
    rejection = events(observer, UniverseAdmissionStage.REFERENCE_WARMUP_REJECTED)[0]
    assert rejection["normalized_symbol"] == "BADREF"
    assert rejection["reason"] == "MISSING_DATA"
    assert [e["normalized_symbol"] for e in events(
        observer, UniverseAdmissionStage.UNIVERSE_ADMITTED
    )] == ["GOOD"]

    event = SimpleNamespace(symbol="GOOD")
    engine.consume(event)
    engine.consume(event)
    # A production observer deterministically suppresses the repeated stage identity;
    # this recorder proves the hook is on the active-symbol pipeline boundary only.
    reached = events(observer, UniverseAdmissionStage.SCANNER_EVALUATION_REACHED)
    assert len(reached) == 2
    assert pipeline.events == [event, event]


def test_observer_failure_does_not_escape_scanner_or_change_active_universe():
    observer = RecordingObserver(failing=True)
    decision = SimpleNamespace(
        symbol="GOOD", qualified=True, score=91,
        metrics=SimpleNamespace(
            relative_volume=Decimal("7"), percentage_change=Decimal("20")
        ),
    )
    pipeline = _Pipeline(decision)
    engine = RealtimeScannerEngine(
        SimpleNamespace(select_all=lambda _: SimpleNamespace(
            included=(_symbol("GOOD"),), excluded=()
        )),
        _ReferenceService(), pipeline, admission_observer=observer, clock=lambda: NOW,
    )
    assert engine.refresh_universe((AssetClass.STOCK,)) == ("GOOD",)
    event = SimpleNamespace(symbol="GOOD")
    assert engine.consume(event) is decision
    assert engine.ranked_candidates() == (decision,)
    assert pipeline.events == [event]

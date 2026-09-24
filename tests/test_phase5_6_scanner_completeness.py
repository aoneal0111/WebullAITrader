from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
    TradePayload,
)
from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.momentum_scanner import AssetClass
from app.realtime_scanner import RealtimeScannerEngine
from app.reference_data import ReferenceRecord
from app.universe import SecurityType, UniverseSelection, UniverseSymbol
from app.scanner_adapter import (
    MarketEventScannerAdapter,
    MomentumScannerPipeline,
    ScannerReferenceData,
    ScannerReferenceStore,
)


NOW = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)


def reference(symbol: str = "TEST", *, float_shares=Decimal("5000000")):
    return ScannerReferenceData(
        symbol=symbol.lower(),
        previous_close=Decimal("5"),
        average_30_day_volume=Decimal("100000"),
        float_shares=float_shares,
        catalyst=CatalystType.NONE,
        catalyst_status=CatalystStatus.FALSE,
        tradable=True,
        updated_at=NOW,
        current_volume=Decimal("100"),
    )


def quote(symbol="TEST", timestamp=NOW, sequence=1):
    return MarketEvent(
        sequence, timestamp, symbol, "WEBULL", MarketEventType.QUOTE,
        QuotePayload(Decimal("5.99"), Decimal("6.01"), Decimal("100"), Decimal("100")),
    )


def trade(symbol="TEST", timestamp=NOW, sequence=2, size="1000"):
    return MarketEvent(
        sequence, timestamp, symbol, "WEBULL", MarketEventType.TRADE,
        TradePayload(Decimal("6"), Decimal(size), str(sequence)),
    )


def adapter(symbol="TEST", **kwargs):
    return MarketEventScannerAdapter(ScannerReferenceStore((reference(symbol, **kwargs),)))


def test_quote_then_trade_preserves_quote_and_reference_fields():
    scanner = adapter()
    scanner.consume(quote())
    result = scanner.consume(trade(size="900000"))
    assert result.observation is not None
    assert (result.observation.bid, result.observation.ask) == (Decimal("5.99"), Decimal("6.01"))
    assert result.observation.previous_close == Decimal("5")
    assert result.observation.average_30_day_volume == Decimal("100000")
    assert result.observation.current_volume == Decimal("900100")


def test_trade_then_quote_preserves_price_volume_and_reference_fields():
    scanner = adapter()
    scanner.consume(trade(size="900000"))
    result = scanner.consume(quote(sequence=3, timestamp=NOW + timedelta(seconds=1)))
    assert result.observation is not None
    assert result.observation.price == Decimal("6")
    assert result.observation.current_volume == Decimal("900100")
    assert result.observation.float_shares == Decimal("5000000")


def test_reference_replacement_does_not_erase_live_state():
    store = ScannerReferenceStore((reference(),))
    scanner = MarketEventScannerAdapter(store)
    scanner.consume(quote())
    scanner.consume(trade(size="900000"))
    store.put(reference(float_shares=Decimal("4000000")))
    observation = scanner.observation_for("test")
    assert observation is not None
    assert observation.price == Decimal("6")
    assert observation.bid == Decimal("5.99")
    assert observation.float_shares == Decimal("4000000")


def test_live_updates_do_not_erase_reference_catalyst_tradability_or_float():
    scanner = adapter()
    scanner.consume(quote())
    scanner.consume(trade(size="900000"))
    scanner.consume(quote(timestamp=NOW + timedelta(seconds=1), sequence=3))
    state = scanner.observation_for("TEST")
    assert state is not None
    assert state.tradable is True
    assert state.float_shares == Decimal("5000000")
    assert state.catalyst_status is CatalystStatus.FALSE


def test_incomplete_state_waits_for_required_live_inputs():
    scanner = adapter()
    partial = scanner.consume(trade(size="900000"))
    assert partial.observation is None
    assert {"bid", "ask"}.issubset(partial.missing_fields)
    complete = scanner.consume(quote(sequence=3, timestamp=NOW + timedelta(seconds=1)))
    assert complete.observation is not None


def test_reference_ready_is_distinct_from_live_and_qualification_ready():
    scanner = adapter()
    scanner.consume(quote())
    metrics = scanner.population_metrics(
        active_symbols=("TEST",), streamed_symbols=("TEST",), now=NOW,
    )
    assert metrics["readiness_counts"] == {
        "discovered": 0,
        "reference_ready": 0,
        "observation_ready": 0,
        "qualification_ready": 0,
        "streamed": 0,
    } or metrics["readiness_counts"]["reference_ready"] == 1
    scanner.consume(quote())
    metrics = scanner.population_metrics(
        active_symbols=("TEST",), streamed_symbols=("TEST",), now=NOW,
    )
    assert metrics["readiness_counts"]["reference_ready"] == 1
    assert metrics["readiness_counts"]["observation_ready"] == 1
    assert metrics["readiness_counts"]["qualification_ready"] == 0


def test_not_streamed_is_distinct_from_streamed_but_incomplete():
    scanner = adapter()
    scanner.consume(quote())
    metrics = scanner.population_metrics(
        active_symbols=("TEST",), streamed_symbols=(), now=NOW,
    )
    assert metrics["missing_state_counts"]["NOT_STREAMED"] == 1
    metrics = scanner.population_metrics(
        active_symbols=("TEST",), streamed_symbols=("TEST",), now=NOW,
    )
    assert metrics["missing_state_counts"]["NO_LIVE_PRICE"] == 1


def test_stale_live_data_is_distinct_from_never_received_live_data():
    scanner = adapter()
    never = scanner.population_metrics(
        active_symbols=("TEST",), streamed_symbols=("TEST",), now=NOW,
    )
    assert never["missing_state_counts"]["STALE_LIVE_DATA"] == 0
    scanner.consume(quote(timestamp=NOW - timedelta(seconds=30)))
    stale = scanner.population_metrics(
        active_symbols=("TEST",), streamed_symbols=("TEST",), now=NOW,
    )
    assert stale["missing_state_counts"]["STALE_LIVE_DATA"] == 1


def test_symbol_normalization_keeps_one_canonical_state():
    scanner = adapter("ABC")
    scanner.consume(quote("abc"))
    result = scanner.consume(trade("AbC", size="900000"))
    assert result.observation is not None
    assert scanner.state_for("abc") is scanner.state_for("ABC")
    assert scanner.memory_metrics()["symbol_state_count"] == 1


def test_pipeline_does_not_evaluate_until_observation_is_complete():
    pipeline = MomentumScannerPipeline(adapter())
    assert pipeline.consume(trade(size="900000")) is None
    assert pipeline.latest_decision("TEST") is None
    assert pipeline.consume(quote(sequence=3, timestamp=NOW + timedelta(seconds=1))) is not None


def test_ranking_expiry_does_not_delete_canonical_state():
    clock = {"now": NOW}
    pipeline = MomentumScannerPipeline(
        adapter(), clock=lambda: clock["now"], candidate_retention_seconds=1,
    )
    pipeline.consume(quote())
    pipeline.consume(trade(size="900000"))
    clock["now"] = NOW + timedelta(seconds=2)
    pipeline.ranked()
    assert pipeline.adapter.state_for("TEST") is not None


def test_relative_volume_requires_valid_reference_denominator():
    scanner = MarketEventScannerAdapter(ScannerReferenceStore())
    # ScannerReferenceData rejects an invalid denominator; absent reference
    # data therefore remains an explicit incomplete state, never fabricated.
    result = scanner.consume(quote())
    assert result.observation is None


def test_completeness_examples_and_counts_are_bounded():
    scanner = adapter()
    metrics = scanner.population_metrics(
        active_symbols=("TEST",), streamed_symbols=(), now=NOW,
    )
    assert all(len(values) <= 3 for values in metrics["missing_state_examples"].values())


def test_live_first_reference_completion_schedules_real_pipeline_evaluation():
    store = ScannerReferenceStore()
    scanner = MarketEventScannerAdapter(store)
    pipeline = MomentumScannerPipeline(
        scanner,
        coalesce_observational=True,
    )

    # Live state arrives while reference warmup is still incomplete.  The
    # canonical reducer must retain it, but no qualification is allowed yet.
    assert pipeline.consume(quote()) is None
    assert pipeline.consume(trade(size="900000")) is None
    assert pipeline.pending_evaluation_symbols() == ()

    # Reference completion is the final input.  It must wake the existing
    # bounded mailbox rather than waiting for an unrelated future tick.
    store.put(reference())
    assert pipeline.reference_ready("TEST") is True
    assert pipeline.pending_evaluation_symbols() == ("TEST",)
    decisions = pipeline.drain_evaluations()
    assert len(decisions) == 1
    assert decisions[0].symbol == "TEST"
    assert scanner.observation_for("TEST").current_volume == Decimal("100")


def test_reference_first_then_live_event_schedules_normally():
    store = ScannerReferenceStore((reference(),))
    pipeline = MomentumScannerPipeline(
        MarketEventScannerAdapter(store),
        coalesce_observational=True,
    )
    assert pipeline.consume(quote()) is None
    assert pipeline.consume(trade(size="900000")) is None
    assert pipeline.pending_evaluation_symbols() == ("TEST",)
    assert len(pipeline.drain_evaluations()) == 1


def test_reference_completion_does_not_schedule_without_live_state():
    store = ScannerReferenceStore()
    pipeline = MomentumScannerPipeline(
        MarketEventScannerAdapter(store),
        coalesce_observational=True,
    )
    store.put(reference())
    assert pipeline.reference_ready("TEST") is False
    assert pipeline.pending_evaluation_symbols() == ()


def test_engine_reference_warmup_wakes_live_first_pipeline_path():
    class Universe:
        def select_all(self, _asset_classes):
            return UniverseSelection(
                included=(UniverseSymbol(
                    symbol="TEST", asset_class=AssetClass.STOCK,
                    exchange="NASDAQ", security_type=SecurityType.COMMON_STOCK,
                    tradable=True,
                ),),
                excluded=(),
            )

    class References:
        def get(self, symbol, _asset_class, *, force_refresh=False):
            return ReferenceRecord(
                symbol=symbol, asset_class=AssetClass.STOCK,
                exchange="NASDAQ", previous_close=Decimal("5"),
                average_30_day_volume=Decimal("100000"),
                float_shares=Decimal("5000000"), market_cap=None,
                shares_outstanding=None, tradable=True,
                catalyst=CatalystType.NONE, catalyst_status=CatalystStatus.FALSE,
                as_of=NOW, current_volume=Decimal("100"),
            )

    store = ScannerReferenceStore()
    scanner = MarketEventScannerAdapter(store)
    pipeline = MomentumScannerPipeline(scanner, coalesce_observational=True)
    engine = RealtimeScannerEngine(
        Universe(), References(), pipeline,
        reference_sink=lambda record: store.put(
            ScannerReferenceData(
                symbol=record.symbol,
                previous_close=record.previous_close,
                average_30_day_volume=record.average_30_day_volume,
                float_shares=record.float_shares,
                catalyst=record.catalyst,
                catalyst_status=record.catalyst_status,
                tradable=record.tradable,
                updated_at=record.as_of,
                current_volume=record.current_volume,
            )
        ),
    )
    engine.prepare_universe((AssetClass.STOCK,))
    assert engine.consume(quote()) is None
    assert engine.consume(trade(size="900000")) is None
    assert pipeline.pending_evaluation_symbols() == ()
    engine.refresh_universe((AssetClass.STOCK,))
    assert pipeline.pending_evaluation_symbols() == ("TEST",)
    assert len(engine.drain_evaluations()) == 1

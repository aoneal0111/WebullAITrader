from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
    ResumePayload,
    TradePayload,
    TradingHaltPayload,
)
from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.scanner_adapter import (
    MarketEventScannerAdapter,
    MomentumScannerPipeline,
    ScannerReferenceData,
    ScannerReferenceStore,
)


NOW = datetime(2026, 7, 20, 15, 0, tzinfo=timezone.utc)


def reference_data(
    *,
    float_shares: Decimal | None = Decimal("5000000"),
    catalyst: CatalystType | None = None,
    catalyst_status: CatalystStatus = CatalystStatus.TRUE,
    current_volume: Decimal | None = None,
) -> ScannerReferenceData:
    return ScannerReferenceData(
        symbol="TEST",
        previous_close=Decimal("5"),
        average_30_day_volume=Decimal("100000"),
        float_shares=float_shares,
        catalyst=(
            catalyst
            if catalyst is not None
            else next(
                item for item in CatalystType if item is not CatalystType.NONE
            )
        ),
        catalyst_status=catalyst_status,
        tradable=True,
        updated_at=NOW,
        current_volume=current_volume,
    )


def quote_event() -> MarketEvent:
    return MarketEvent(
        sequence=1,
        timestamp=NOW,
        symbol="TEST",
        source="WEBULL",
        event_type=MarketEventType.QUOTE,
        payload=QuotePayload(
            bid=Decimal("5.99"),
            ask=Decimal("6.01"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        ),
    )


def trade_event(
    *,
    sequence: int = 2,
    size: Decimal = Decimal("600000"),
) -> MarketEvent:
    return MarketEvent(
        sequence=sequence,
        timestamp=NOW,
        symbol="TEST",
        source="WEBULL",
        event_type=MarketEventType.TRADE,
        payload=TradePayload(
            price=Decimal("6"),
            size=size,
            trade_id=str(sequence),
        ),
    )


def test_adapter_fails_closed_without_reference_data() -> None:
    adapter = MarketEventScannerAdapter(ScannerReferenceStore())

    adapter.consume(quote_event())
    result = adapter.consume(trade_event())

    assert result is not None
    assert result.observation is None
    assert "previous_close" in result.missing_fields
    assert "float_shares" in result.missing_fields


def test_adapter_fails_closed_without_quote() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)

    result = adapter.consume(trade_event())

    assert result is not None
    assert result.observation is None
    assert "bid" in result.missing_fields
    assert "ask" in result.missing_fields


def test_adapter_builds_complete_observation() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)

    adapter.consume(quote_event())
    result = adapter.consume(trade_event())

    assert result is not None
    assert result.observation is not None
    assert result.observation.symbol == "TEST"
    assert result.observation.price == Decimal("6")
    assert result.observation.current_volume == Decimal("600000")
    assert result.observation.bid == Decimal("5.99")
    assert result.observation.ask == Decimal("6.01")
    assert result.missing_fields == ()


def test_trade_volume_accumulates() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)

    adapter.consume(quote_event())
    adapter.consume(
        trade_event(sequence=2, size=Decimal("300000"))
    )
    result = adapter.consume(
        trade_event(sequence=3, size=Decimal("400000"))
    )

    assert result is not None
    assert result.observation is not None
    assert result.observation.current_volume == Decimal("700000")


def test_discovery_volume_seeds_state_and_survives_all_channel_orders() -> None:
    store = ScannerReferenceStore((
        reference_data(current_volume=Decimal("1500000")),
    ))
    adapter = MarketEventScannerAdapter(store)

    quoted = adapter.consume(quote_event())
    traded = adapter.consume(trade_event(size=Decimal("100")))
    older_snapshot = adapter.consume(replace(
        trade_event(sequence=3),
        timestamp=NOW - timedelta(seconds=1),
        payload=TradePayload(
            price=Decimal("5.95"), size=Decimal("1400000"), trade_id="snapshot"
        ),
    ))
    newer_snapshot = adapter.consume(replace(
        trade_event(sequence=4),
        timestamp=NOW + timedelta(seconds=1),
        payload=TradePayload(
            price=Decimal("6.05"), size=Decimal("1600000"), trade_id="snapshot"
        ),
    ))

    assert quoted is not None and quoted.state.cumulative_volume == Decimal("1500000")
    assert traded is not None and traded.state.cumulative_volume == Decimal("1500100")
    assert older_snapshot is not None and older_snapshot.state.cumulative_volume == Decimal("1500100")
    assert newer_snapshot is not None and newer_snapshot.state.cumulative_volume == Decimal("1600000")


def test_interleaved_channel_timestamps_keep_fresh_fields_and_coherent_volume() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)
    newer = NOW + timedelta(seconds=2)
    newest = NOW + timedelta(seconds=3)

    adapter.consume(replace(quote_event(), timestamp=newer))
    adapter.consume(replace(trade_event(), timestamp=newest, payload=TradePayload(
        price=Decimal("6.10"), size=Decimal("10"), trade_id="tick-new"
    )))
    result = adapter.consume(replace(trade_event(sequence=3), timestamp=NOW, payload=TradePayload(
        price=Decimal("6"), size=Decimal("1000000"), trade_id="snapshot"
    )))

    assert result is not None and result.observation is not None
    assert result.observation.timestamp == newest
    assert result.observation.bid == Decimal("5.99")
    assert result.observation.price == Decimal("6.10")
    assert result.observation.current_volume == Decimal("1000000")


def test_delayed_tick_already_covered_by_snapshot_does_not_double_count() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)
    adapter.consume(quote_event())
    snapshot_time = NOW + timedelta(seconds=2)
    adapter.consume(replace(trade_event(), timestamp=snapshot_time, payload=TradePayload(
        price=Decimal("6"), size=Decimal("1000000"), trade_id="snapshot"
    )))

    result = adapter.consume(replace(trade_event(sequence=3), timestamp=NOW + timedelta(seconds=1), payload=TradePayload(
        price=Decimal("5.90"), size=Decimal("100"), trade_id="tick-delayed"
    )))

    assert result is not None and result.observation is not None
    assert result.observation.price == Decimal("6")
    assert result.observation.current_volume == Decimal("1000000")


def test_halt_and_resume_update_observation() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)

    adapter.consume(quote_event())
    adapter.consume(trade_event())

    halted = adapter.consume(
        MarketEvent(
            sequence=3,
            timestamp=NOW,
            symbol="TEST",
            source="WEBULL",
            event_type=MarketEventType.TRADING_HALT,
            payload=TradingHaltPayload(reason="volatility"),
        )
    )

    assert halted is not None
    assert halted.observation is not None
    assert halted.observation.halted is True

    resumed = adapter.consume(
        MarketEvent(
            sequence=4,
            timestamp=NOW,
            symbol="TEST",
            source="WEBULL",
            event_type=MarketEventType.RESUME,
            payload=ResumePayload(reason="resumed"),
        )
    )

    assert resumed is not None
    assert resumed.observation is not None
    assert resumed.observation.halted is False


def test_missing_float_fails_closed() -> None:
    store = ScannerReferenceStore(
        (reference_data(float_shares=None),)
    )
    adapter = MarketEventScannerAdapter(store)

    adapter.consume(quote_event())
    result = adapter.consume(trade_event())

    assert result is not None
    assert result.observation is None
    assert result.missing_fields == ("float_shares",)


def test_pipeline_evaluates_and_ranks_candidate() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)
    pipeline = MomentumScannerPipeline(adapter)

    assert pipeline.consume(quote_event()) is None

    decision = pipeline.consume(trade_event(size=Decimal("1000000")))

    assert decision is not None
    assert decision.symbol == "TEST"
    assert decision.qualified is True
    assert pipeline.latest_decision("test") == decision
    assert pipeline.ranked(limit=10) == (decision,)


def test_pipeline_aggregates_rejections_and_catalyst_availability() -> None:
    store = ScannerReferenceStore((reference_data(
        catalyst=CatalystType.NONE,
        catalyst_status=CatalystStatus.UNAVAILABLE,
    ),))
    pipeline = MomentumScannerPipeline(MarketEventScannerAdapter(store))

    pipeline.consume(quote_event())
    decision = pipeline.consume(trade_event(size=Decimal("1000000")))
    diagnostics = pipeline.qualification_diagnostics(example_limit=1)

    assert decision is not None
    assert decision.failed_rules == ("news_catalyst",)
    assert diagnostics.evaluated == 1
    assert diagnostics.complete == 1
    assert diagnostics.qualified == 0
    assert dict(diagnostics.rejection_counts)["news_catalyst"] == 1
    assert dict(diagnostics.catalyst_counts) == {
        "TRUE": 0,
        "FALSE": 0,
        "UNKNOWN": 0,
        "UNAVAILABLE": 1,
    }
    assert diagnostics.otherwise_qualified_with_catalyst == 1
    assert diagnostics.near_qualified_symbols == ("TEST",)


def test_adapter_ignores_events_without_symbols() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)

    event = MarketEvent(
        sequence=1,
        timestamp=NOW,
        symbol=None,
        source="WEBULL",
        event_type=MarketEventType.QUOTE,
        payload=QuotePayload(
            bid=Decimal("5.99"),
            ask=Decimal("6.01"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        ),
    )

    assert adapter.consume(event) is None



def test_price_observer_receives_fresh_trade() -> None:
    store = ScannerReferenceStore((reference_data(),))
    observed = []

    adapter = MarketEventScannerAdapter(
        store,
        price_observer=lambda symbol, timestamp, price: observed.append(
            (symbol, timestamp, price)
        ),
    )

    event = trade_event()
    adapter.consume(event)

    assert observed == [("TEST", event.timestamp, event.payload.price)]


def test_price_observer_ignores_stale_trade() -> None:
    store = ScannerReferenceStore((reference_data(),))
    observed = []

    adapter = MarketEventScannerAdapter(
        store,
        price_observer=lambda symbol, timestamp, price: observed.append(
            (symbol, timestamp, price)
        ),
    )

    current = trade_event()
    adapter.consume(current)

    stale = MarketEvent(
        sequence=current.sequence + 1,
        timestamp=current.timestamp - timedelta(seconds=1),
        symbol=current.symbol,
        source=current.source,
        event_type=MarketEventType.TRADE,
        payload=TradePayload(
            price=Decimal("4.00"),
            size=Decimal("100"),
            trade_id="stale-trade",
        ),
    )
    adapter.consume(stale)

    assert observed == [
        ("TEST", current.timestamp, current.payload.price),
    ]


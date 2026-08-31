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
from app.momentum_scanner.models import CatalystStatus, CatalystType, FloatProvenance
from app.momentum_scanner.rules import MomentumScannerConfig
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
    float_provenance: FloatProvenance = FloatProvenance.AUTHORITATIVE_FLOAT,
    catalyst: CatalystType | None = None,
    catalyst_status: CatalystStatus = CatalystStatus.TRUE,
    current_volume: Decimal | None = None,
) -> ScannerReferenceData:
    return ScannerReferenceData(
        symbol="TEST",
        previous_close=Decimal("5"),
        average_30_day_volume=Decimal("100000"),
        float_shares=float_shares,
        float_provenance=float_provenance,
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


def test_newer_trade_replaces_last_price_and_other_symbol_does_not_refresh_it() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)
    received = NOW + timedelta(milliseconds=25)

    adapter.consume(replace(quote_event(), received_timestamp=received))
    first = adapter.consume(replace(
        trade_event(),
        payload=TradePayload(Decimal("2.485"), Decimal("1"), "old"),
        received_timestamp=received,
    ))
    newer_time = NOW + timedelta(seconds=1)
    newer = adapter.consume(replace(
        trade_event(sequence=3), timestamp=newer_time,
        payload=TradePayload(Decimal("2.68"), Decimal("1"), "new"),
        received_timestamp=newer_time + timedelta(milliseconds=20),
    ))
    adapter.consume(MarketEvent(
        4, newer_time + timedelta(seconds=1), "OTHER", "WEBULL",
        MarketEventType.TRADE,
        TradePayload(Decimal("9"), Decimal("1"), "other"),
        newer_time + timedelta(seconds=1, milliseconds=20),
    ))

    assert first is not None and first.state.last_price == Decimal("2.485")
    assert newer is not None and newer.state.last_price == Decimal("2.68")
    state = adapter.state_for("TEST")
    assert state is not None
    assert state.last_price == Decimal("2.68")
    assert state.last_price_timestamp == newer_time
    assert state.last_price_received_timestamp == newer_time + timedelta(milliseconds=20)


def test_volume_only_snapshot_does_not_refresh_retained_last_price_timestamp() -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)
    adapter.consume(quote_event())
    adapter.consume(trade_event())

    snapshot_time = NOW + timedelta(seconds=30)
    result = adapter.consume(MarketEvent(
        3, snapshot_time, "TEST", "WEBULL", MarketEventType.TRADE,
        TradePayload(Decimal("6"), Decimal("900000"), "snapshot-retained-price"),
        snapshot_time,
    ))

    assert result is not None
    assert result.state.snapshot_timestamp == snapshot_time
    assert result.state.last_price_timestamp == NOW


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


def test_processing_delayed_detection_counts_every_event_but_aggregates_logs(
    caplog,
) -> None:
    store = ScannerReferenceStore((reference_data(),))
    adapter = MarketEventScannerAdapter(store)
    pipeline = MomentumScannerPipeline(
        adapter,
        clock=lambda: NOW + timedelta(seconds=10),
    )
    pipeline.consume(replace(quote_event(), received_timestamp=NOW))

    with caplog.at_level("INFO", logger="atlas.scanner"):
        for sequence in range(2, 2_003):
            pipeline.consume(replace(
                trade_event(sequence=sequence, size=Decimal("1")),
                received_timestamp=NOW,
            ))
        pipeline._clock = lambda: NOW + timedelta(seconds=1)
        pipeline.consume(replace(
            trade_event(sequence=2_003, size=Decimal("1")),
            received_timestamp=NOW,
        ))

    delayed = [
        record for record in caplog.records
        if "event_type=market_event_processing_delayed" in record.message
    ]
    recovered = [
        record for record in caplog.records
        if "event_type=market_event_processing_recovered" in record.message
    ]
    assert pipeline._processing_delay_count == 2_001
    assert len(delayed) == 3
    assert "delayed_event_count=2000" in delayed[-1].message
    assert len(recovered) == 1


def test_pipeline_aggregates_rejections_and_catalyst_availability() -> None:
    store = ScannerReferenceStore((reference_data(
        catalyst=CatalystType.NONE,
        catalyst_status=CatalystStatus.UNAVAILABLE,
    ),))
    pipeline = MomentumScannerPipeline(
        MarketEventScannerAdapter(store),
        MomentumScannerConfig.conservative_v1(),
    )

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


def test_qualification_diagnostics_report_unverified_float_proxy() -> None:
    store = ScannerReferenceStore(
        (
            reference_data(
                float_shares=Decimal("50000000"),
                float_provenance=FloatProvenance.MARKET_CAP_PRICE_PROXY,
            ),
        )
    )
    pipeline = MomentumScannerPipeline(
        MarketEventScannerAdapter(store),
        MomentumScannerConfig.conservative_v1(),
    )

    pipeline.consume(quote_event())
    decision = pipeline.consume(trade_event(size=Decimal("1000000")))
    diagnostics = pipeline.qualification_diagnostics()

    assert decision is not None
    assert decision.qualified is False
    assert "float_verified" in decision.failed_rules
    assert "low_float" not in decision.failed_rules

    rejection_counts = dict(diagnostics.rejection_counts)
    assert rejection_counts["float_verified"] == 1
    assert rejection_counts["low_float"] == 0


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




def test_pipeline_reset_symbol_clears_adapter_and_cached_decision() -> None:
    store = ScannerReferenceStore()
    store.put(
        ScannerReferenceData(
            symbol="XYZ",
            previous_close=Decimal("10"),
            average_30_day_volume=Decimal("1000000"),
            float_shares=Decimal("5000000"),
        )
    )
    adapter = MarketEventScannerAdapter(store)
    pipeline = MomentumScannerPipeline(adapter)

    event = MarketEvent(
        sequence=1,
        timestamp=NOW,
        symbol="XYZ",
        source="test",
        event_type=MarketEventType.QUOTE,
        payload=QuotePayload(
            bid=Decimal("11"),
            ask=Decimal("11.10"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        ),
    )

    pipeline.consume(event)

    assert adapter.state_for("XYZ") is not None

    pipeline.reset_symbol("XYZ")

    assert adapter.state_for("XYZ") is None
    assert pipeline.latest_decision("XYZ") is None


def test_stream_session_reset_allows_lower_timestamp_new_baseline() -> None:
    store = ScannerReferenceStore()
    store.put(
        ScannerReferenceData(
            symbol="XYZ",
            previous_close=Decimal("10"),
            average_30_day_volume=Decimal("1000000"),
            float_shares=Decimal("5000000"),
        )
    )

    adapter = MarketEventScannerAdapter(store)
    pipeline = MomentumScannerPipeline(adapter)

    old_session_timestamp = datetime(
        2026,
        8,
        25,
        17,
        0,
        tzinfo=timezone.utc,
    )
    replacement_session_timestamp = datetime(
        2026,
        8,
        25,
        16,
        59,
        tzinfo=timezone.utc,
    )

    old_session_quote = MarketEvent(
        sequence=1,
        timestamp=old_session_timestamp,
        symbol="XYZ",
        source="test",
        event_type=MarketEventType.QUOTE,
        payload=QuotePayload(
            bid=Decimal("11.00"),
            ask=Decimal("11.10"),
            bid_size=Decimal("100"),
            ask_size=Decimal("100"),
        ),
    )

    lower_timestamp_quote = MarketEvent(
        sequence=2,
        timestamp=replacement_session_timestamp,
        symbol="XYZ",
        source="test",
        event_type=MarketEventType.QUOTE,
        payload=QuotePayload(
            bid=Decimal("12.00"),
            ask=Decimal("12.10"),
            bid_size=Decimal("200"),
            ask_size=Decimal("200"),
        ),
    )

    pipeline.consume(old_session_quote)

    original = adapter.state_for("XYZ")

    assert original is not None
    assert original.quote_timestamp == old_session_timestamp
    assert original.bid == Decimal("11.00")
    assert original.ask == Decimal("11.10")

    # Within the same stream session, a timestamp regression remains rejected.
    pipeline.consume(lower_timestamp_quote)

    rejected = adapter.state_for("XYZ")

    assert rejected is not None
    assert rejected.quote_timestamp == old_session_timestamp
    assert rejected.bid == Decimal("11.00")
    assert rejected.ask == Decimal("11.10")

    # A transport session replacement creates a new ordering boundary.
    pipeline.reset_symbol("XYZ")

    assert adapter.state_for("XYZ") is None
    assert pipeline.latest_decision("XYZ") is None

    pipeline.consume(lower_timestamp_quote)

    recovered = adapter.state_for("XYZ")

    assert recovered is not None
    assert recovered.quote_timestamp == replacement_session_timestamp
    assert recovered.timestamp == replacement_session_timestamp
    assert recovered.bid == Decimal("12.00")
    assert recovered.ask == Decimal("12.10")

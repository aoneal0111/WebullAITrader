from datetime import datetime, timezone
from decimal import Decimal

from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
    ResumePayload,
    TradePayload,
    TradingHaltPayload,
)
from app.momentum_scanner.models import CatalystType
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
) -> ScannerReferenceData:
    return ScannerReferenceData(
        symbol="TEST",
        previous_close=Decimal("5"),
        average_30_day_volume=Decimal("100000"),
        float_shares=float_shares,
        catalyst=next(item for item in CatalystType if item is not CatalystType.NONE),
        tradable=True,
        updated_at=NOW,
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



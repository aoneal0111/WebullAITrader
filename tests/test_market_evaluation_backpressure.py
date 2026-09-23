from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market_data.models import (
    MarketEvent, MarketEventType, QuotePayload, ResumePayload,
    TradePayload, TradingHaltPayload, VolumeSemantics,
)
from app.scanner_adapter import (
    MarketEventScannerAdapter, MomentumScannerPipeline,
    ScannerReferenceData, ScannerReferenceStore,
)
from app.scanner_adapter.evaluation_mailbox import LatestEvaluationMailbox
from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.performance_diagnostics import performance_diagnostics


NOW = datetime(2026, 9, 23, 13, 0, tzinfo=UTC)


def _reference(symbol: str) -> ScannerReferenceData:
    return ScannerReferenceData(
        symbol=symbol,
        previous_close=Decimal("5"),
        average_30_day_volume=Decimal("100000"),
        float_shares=Decimal("5000000"),
        catalyst=CatalystType.EARNINGS,
        catalyst_status=CatalystStatus.TRUE,
        current_volume=Decimal("0"),
        updated_at=NOW,
    )


def _quote(symbol: str, sequence: int, at: datetime) -> MarketEvent:
    return MarketEvent(
        sequence, at, symbol, "WEBULL", MarketEventType.QUOTE,
        QuotePayload(Decimal("5.99"), Decimal("6.01"), Decimal("100"), Decimal("100")),
    )


def _trade(
    symbol: str,
    sequence: int,
    at: datetime,
    price: str,
    size: str = "10",
    *,
    semantics: VolumeSemantics = VolumeSemantics.TRADE_SIZE,
    trade_id: str | None = None,
) -> MarketEvent:
    return MarketEvent(
        sequence, at, symbol, "WEBULL", MarketEventType.TRADE,
        TradePayload(
            Decimal(price), Decimal(size), trade_id or str(sequence), semantics,
        ),
    )


def test_mailbox_supersedes_same_symbol_and_preserves_symbol_fairness() -> None:
    mailbox = LatestEvaluationMailbox(capacity=8)
    for version in range(1, 501):
        assert mailbox.enqueue("WHLR", version, NOW, version)
    assert mailbox.enqueue("IPDN", 1, NOW, "ipdn")
    assert mailbox.enqueue("LXEH", 1, NOW, "lxeh")

    assert len(mailbox) == 3
    assert mailbox.metrics(now=NOW + timedelta(seconds=1))["symbol_snapshots_superseded"] == 499
    assert [mailbox.pop().symbol for _ in range(3)] == ["WHLR", "IPDN", "LXEH"]
    assert mailbox.metrics()["evaluation_mailbox_high_water"] == 3


def test_lossless_adapter_state_is_reduced_before_latest_evaluation(
    ) -> None:
    store = ScannerReferenceStore((_reference("WHLR"),))
    adapter = MarketEventScannerAdapter(store)
    pipeline = MomentumScannerPipeline(
        adapter, coalesce_observational=True, evaluation_mailbox_capacity=8,
        clock=lambda: NOW,
    )

    pipeline.consume(_quote("WHLR", 1, NOW))
    pipeline.consume(_trade(
        "WHLR", 2, NOW, "6", "100", semantics=VolumeSemantics.ACCUMULATED,
        trade_id="snapshot-1",
    ))
    for sequence in range(3, 203):
        pipeline.consume(_trade(
            "WHLR", sequence, NOW + timedelta(milliseconds=sequence),
            f"{Decimal('6') + Decimal(sequence) / Decimal('1000')}", "10",
        ))

    state = adapter.state_for("WHLR")
    assert state is not None
    assert state.last_price == Decimal("6.202")
    assert state.trade_timestamp == NOW + timedelta(milliseconds=202)
    assert state.quote_timestamp == NOW
    assert state.cumulative_volume == Decimal("2100")
    metrics = pipeline.memory_metrics()
    assert metrics["evaluation_mailbox_size"] == 1
    assert metrics["symbol_snapshots_superseded"] >= 200

    decisions = pipeline.drain_evaluations()
    assert len(decisions) == 1
    assert pipeline.memory_metrics()["evaluation_mailbox_size"] == 0


def test_cross_symbol_fairness_under_hot_symbol_burst() -> None:
    symbols = ("WHLR", "IPDN", "LXEH")
    sink: list[str] = []
    pipeline = MomentumScannerPipeline(
        MarketEventScannerAdapter(
            ScannerReferenceStore(tuple(_reference(symbol) for symbol in symbols))
        ),
        coalesce_observational=True,
        decision_sink=lambda decision: sink.append(decision.symbol),
        clock=lambda: NOW,
    )
    sequence = 1
    for symbol in symbols:
        pipeline.consume(_quote(symbol, sequence, NOW))
        sequence += 1
    for index in range(100):
        pipeline.consume(_trade("WHLR", sequence, NOW, "6"))
        sequence += 1
    pipeline.consume(_trade("IPDN", sequence, NOW, "6"))
    sequence += 1
    pipeline.consume(_trade("LXEH", sequence, NOW, "6"))

    pipeline.drain_evaluations()
    assert set(sink) == set(symbols)
    assert pipeline.memory_metrics()["evaluation_mailbox_high_water"] == 3


def test_stale_evaluation_result_cannot_overwrite_newer_version() -> None:
    store = ScannerReferenceStore((_reference("WHLR"),))
    pipeline = MomentumScannerPipeline(
        MarketEventScannerAdapter(store),
        coalesce_observational=True,
        clock=lambda: NOW,
    )
    pipeline.consume(_quote("WHLR", 1, NOW))
    first = _trade("WHLR", 2, NOW, "6")
    newer = _trade("WHLR", 3, NOW + timedelta(seconds=1), "6.50")
    pipeline.consume(first)
    original = pipeline._evaluate_result
    injected = {"done": False}

    def evaluate(event, result, **kwargs):
        if not injected["done"]:
            injected["done"] = True
            pipeline.consume(newer)
        return original(event, result, **kwargs)

    pipeline._evaluate_result = evaluate
    assert pipeline.drain_evaluations(maximum=1) == ()
    assert pipeline.memory_metrics()["result_version_rejections"] == 1
    latest = pipeline.drain_evaluations()
    assert len(latest) == 1
    assert pipeline.latest_decision("WHLR") == latest[0]


def test_halt_resume_remains_ordered_while_evaluation_is_coalesced() -> None:
    adapter = MarketEventScannerAdapter(
        ScannerReferenceStore((_reference("WHLR"),))
    )
    pipeline = MomentumScannerPipeline(
        adapter, coalesce_observational=True, clock=lambda: NOW,
    )
    pipeline.consume(_quote("WHLR", 1, NOW))
    pipeline.consume(_trade("WHLR", 2, NOW, "6"))
    pipeline.consume(MarketEvent(
        3, NOW + timedelta(seconds=1), "WHLR", "WEBULL",
        MarketEventType.TRADING_HALT, TradingHaltPayload("test"),
    ))
    pipeline.consume(MarketEvent(
        4, NOW + timedelta(seconds=2), "WHLR", "WEBULL",
        MarketEventType.RESUME, ResumePayload("test"),
    ))
    state = adapter.state_for("WHLR")
    assert state is not None and state.halted is False
    assert pipeline.memory_metrics()["evaluation_mailbox_size"] == 1


def test_captured_load_shape_keeps_pending_evaluation_age_below_five_seconds() -> None:
    clock = {"value": NOW}
    pipeline = MomentumScannerPipeline(
        MarketEventScannerAdapter(ScannerReferenceStore((_reference("WHLR"),))),
        coalesce_observational=True,
        clock=lambda: clock["value"],
    )
    pipeline.consume(_quote("WHLR", 1, NOW))
    for sequence in range(2, 3002):
        pipeline.consume(_trade(
            "WHLR", sequence, NOW + timedelta(milliseconds=sequence), "6", "1",
        ))
    clock["value"] = NOW + timedelta(seconds=1)
    metrics = pipeline.memory_metrics()
    assert metrics["raw_callbacks_received"] == 3001
    assert metrics["evaluation_mailbox_size"] == 1
    assert metrics["evaluation_mailbox_high_water"] == 1
    assert metrics["oldest_pending_evaluation_age_seconds"] <= 5.0
    assert metrics["symbol_snapshots_superseded"] >= 2999
    assert len(pipeline.drain_evaluations()) == 1


def test_p1_load_instrumentation_is_bounded_and_reports_stage_timings() -> None:
    symbols = ("WHLR", "IPDN", "LXEH", "ABCD", "EFGH", "IJKL")
    pipeline = MomentumScannerPipeline(
        MarketEventScannerAdapter(
            ScannerReferenceStore(tuple(_reference(symbol) for symbol in symbols))
        ),
        coalesce_observational=True,
        evaluation_mailbox_capacity=16,
        clock=lambda: NOW,
    )
    sequence = 1
    for symbol in symbols:
        pipeline.consume(_quote(symbol, sequence, NOW))
        sequence += 1
    # 47% hot-symbol traffic, interleaved with five other symbols.
    for index in range(3_000):
        symbol = "WHLR" if index % 100 < 47 else symbols[(index % 5) + 1]
        pipeline.consume(_trade(symbol, sequence, NOW, "6", "1"))
        sequence += 1
    metrics = pipeline.memory_metrics()
    assert metrics["evaluation_mailbox_size"] <= len(symbols)
    assert metrics["evaluation_mailbox_high_water"] <= len(symbols)
    assert metrics["symbol_snapshots_superseded"] >= 1_000
    assert len(pipeline.drain_evaluations()) == len(symbols)
    timings = performance_diagnostics.snapshot().component_timings
    assert "evaluation_mailbox_residence" in timings
    assert timings["evaluation_mailbox_residence"]["p99_ms"] < 5_000.0

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

from app.momentum_scanner.models import ScannerDecision, ScannerMetrics
from app.market_data.models import (
    MarketEvent,
    MarketEventType,
    QuotePayload,
    TradePayload,
)
from app.operations.scanner_snapshot_publisher import ScannerSnapshotPublisher
from app.performance_diagnostics import PerformanceDiagnostics
from app.realtime_scanner.models import ScannerSnapshot
from app.scanner_adapter.adapter import MarketEventScannerAdapter
from app.scanner_adapter.models import SymbolScannerState
from app.scanner_adapter.reference_store import ScannerReferenceStore


NOW = datetime(2026, 9, 11, 15, 0, tzinfo=UTC)


def decision(
    symbol: str,
    *,
    qualified: bool,
    score: int,
    failed_rules: tuple[str, ...] = (),
    watching: bool = False,
    scanner_rank: int | None = None,
    current_volume: Decimal | None = None,
    average_volume: Decimal | None = None,
    trade_timestamp: datetime | None = None,
    technical_failed_rules: tuple[str, ...] = (),
) -> ScannerDecision:
    return ScannerDecision(
        symbol=symbol,
        qualified=qualified,
        score=score,
        metrics=ScannerMetrics(
            percentage_change=Decimal("10"),
            relative_volume=Decimal("5"),
            dollar_volume=Decimal("1000000"),
            spread_percent=Decimal("0.2"),
        ),
        passed_rules=(),
        failed_rules=failed_rules,
        technical_qualifies_without_catalyst=watching,
        scanner_rank=scanner_rank,
        current_volume=current_volume,
        average_30_day_volume=average_volume,
        trade_timestamp=trade_timestamp,
        technical_failed_rules=technical_failed_rules,
    )


def publish(decisions: tuple[ScannerDecision, ...], diagnostics: PerformanceDiagnostics):
    publisher = ScannerSnapshotPublisher(
        lambda _event: None,
        lambda: 1,
        source="test",
        stale_after=timedelta(minutes=5),
        diagnostics=diagnostics,
    )
    publisher.publish(
        ScannerSnapshot(
            timestamp=NOW,
            active_symbols=tuple(item.symbol for item in decisions),
            decisions=decisions,
            ranked_candidates=tuple(
                item for item in decisions if item.qualified
            ),
            processed_events=1,
            ignored_events=0,
            reference_failures=(),
        ),
        cycle=1,
        now=NOW,
    )


def test_rejected_decisions_are_ranked_but_displayed_population_is_separate():
    diagnostics = PerformanceDiagnostics()
    publish(
        (
            decision("HIDDEN", qualified=False, score=99, failed_rules=("price_range",), scanner_rank=1),
            decision("WATCH", qualified=False, score=50, failed_rules=("news_catalyst",), watching=True, scanner_rank=2),
        ),
        diagnostics,
    )

    population = diagnostics.scanner_population_metrics()
    assert population["all_decision_rank_count"] == 2
    assert population["displayed_candidate_count"] == 2
    assert population["hidden_rejected_count"] == 0
    assert population["highest_hidden_rejected_score"] is None
    assert population["hidden_ranked_ahead_by_displayed_candidate"] == {
        "HIDDEN": 0,
        "WATCH": 0,
    }
    assert population["watching_count"] == 1


def test_incomplete_adapter_states_increment_exact_missing_fields():
    adapter = MarketEventScannerAdapter(ScannerReferenceStore())
    adapter._states["EMPTY"] = SymbolScannerState(symbol="EMPTY")

    metrics = adapter.population_metrics()

    assert metrics["adapter_state_count"] == 1
    assert metrics["missing_field_counts"] == {
        "timestamp": 1,
        "last_price": 1,
        "bid": 1,
        "ask": 1,
        "current_volume": 1,
        "previous_close": 1,
        "average_30_day_volume": 1,
        "float_shares": 1,
        "catalyst": 1,
        "tradable": 1,
        "other": 0,
    }


def test_population_diagnostics_are_bounded():
    diagnostics = PerformanceDiagnostics()
    decisions = tuple(
        decision(
            f"S{index:02d}",
            qualified=False,
            score=100 - index,
            failed_rules=("price_range", "spread", "dollar_volume"),
            scanner_rank=index + 1,
        )
        for index in range(30)
    )
    publish(decisions, diagnostics)

    population = diagnostics.scanner_population_metrics()
    assert len(population["top_sample"]) == 25
    assert population["hidden_rejected_count"] == 5
    assert population["failed_rule_distribution"]["3_plus"] == 30


def test_population_diagnostics_are_durable(tmp_path):
    diagnostics = PerformanceDiagnostics()
    artifact = tmp_path / "scanner-population.json"
    diagnostics.start_run(artifact_path=artifact, flush_seconds=0.05)
    diagnostics.record_scanner_population_base(
        active_symbols=4,
        adapter_state_count=6,
        missing_field_counts={"bid": 2},
    )
    diagnostics.record_scanner_population_display({"qualified_count": 1})
    diagnostics.finish_run()

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "FINAL"
    assert payload["scanner_population"]["active_symbols"] == 4
    assert payload["scanner_population"]["adapter_state_count"] == 6
    assert payload["scanner_population"]["qualified_count"] == 1


def test_rvol_operands_use_production_decimal_ratio():
    diagnostics = PerformanceDiagnostics()
    publish(
        (decision(
            "RVOL",
            qualified=False,
            score=10,
            failed_rules=("relative_volume",),
            scanner_rank=1,
            current_volume=Decimal("125"),
            average_volume=Decimal("50"),
        ),),
        diagnostics,
    )

    sample = diagnostics.scanner_population_metrics()["top_sample"][0]
    assert sample["current_volume"] == "125"
    assert sample["average_30_day_volume"] == "50"
    assert sample["computed_relative_volume"] == "2.5"
    assert sample["relative_volume_passed"] is False


def test_invalid_rvol_denominator_is_not_reported_as_a_valid_ratio():
    diagnostics = PerformanceDiagnostics()
    publish(
        (decision(
            "ZERO",
            qualified=False,
            score=1,
            failed_rules=("relative_volume",),
            scanner_rank=1,
            current_volume=Decimal("10"),
            average_volume=Decimal("0"),
        ),),
        diagnostics,
    )

    assert diagnostics.scanner_population_metrics()["top_sample"][0][
        "computed_relative_volume"
    ] is None


def _event(event_type, timestamp, payload):
    return MarketEvent(
        sequence=1,
        timestamp=timestamp,
        symbol="TEST",
        source="TEST",
        event_type=event_type,
        payload=payload,
    )


def _reference_store():
    from app.scanner_adapter.models import ScannerReferenceData

    return ScannerReferenceStore((ScannerReferenceData(
        symbol="TEST",
        previous_close=Decimal("5"),
        average_30_day_volume=Decimal("100"),
        float_shares=Decimal("1000"),
    ),))


def test_adapter_merges_quote_and_trade_in_either_order():
    for events in (
        (
            _event(MarketEventType.QUOTE, NOW, QuotePayload(Decimal("5.9"), Decimal("6.1"), Decimal("10"), Decimal("10"))),
            _event(MarketEventType.TRADE, NOW, TradePayload(Decimal("6"), Decimal("100"), "trade")),
        ),
        (
            _event(MarketEventType.TRADE, NOW, TradePayload(Decimal("6"), Decimal("100"), "trade")),
            _event(MarketEventType.QUOTE, NOW, QuotePayload(Decimal("5.9"), Decimal("6.1"), Decimal("10"), Decimal("10"))),
        ),
    ):
        adapter = MarketEventScannerAdapter(_reference_store())
        result = None
        for event in events:
            result = adapter.consume(event)
        assert result is not None and result.observation is not None
        assert result.observation.price == Decimal("6")
        assert result.observation.bid == Decimal("5.9")
        assert result.observation.ask == Decimal("6.1")


def test_completeness_transition_is_one_bounded_record_with_age_and_recovery():
    store = ScannerReferenceStore()
    adapter = MarketEventScannerAdapter(store)
    adapter.consume(_event(
        MarketEventType.TRADE,
        NOW,
        TradePayload(Decimal("6"), Decimal("100"), "trade"),
    ))
    incomplete = adapter.population_metrics(
        active_symbols=("TEST",), now=NOW + timedelta(seconds=5)
    )
    record = incomplete["completeness_transitions"]["TEST"]
    assert record["complete"] is False
    assert record["incomplete_age_ms"] == 5000.0
    assert record["incomplete_transition_count"] == 1

    store.put(_reference_store().get("TEST"))
    adapter.consume(_event(
        MarketEventType.QUOTE,
        NOW + timedelta(seconds=6),
        QuotePayload(Decimal("5.9"), Decimal("6.1"), Decimal("10"), Decimal("10")),
    ))
    recovered = adapter.population_metrics(
        active_symbols=("TEST",), now=NOW + timedelta(seconds=7)
    )
    record = recovered["completeness_transitions"]["TEST"]
    assert record["complete"] is True
    assert record["incomplete_age_ms"] is None
    assert record["complete_transition_count"] == 1


def test_all_view_publishes_complete_ineligible_decisions_without_qualifying_them():
    diagnostics = PerformanceDiagnostics()
    events = []
    publisher = ScannerSnapshotPublisher(
        events.append,
        lambda: 1,
        source="test",
        stale_after=timedelta(minutes=5),
        diagnostics=diagnostics,
    )
    rejected = decision(
        "REJECTED",
        qualified=False,
        score=90,
        failed_rules=("relative_volume", "spread"),
        scanner_rank=1,
    )
    near_miss = decision(
        "NEAR",
        qualified=False,
        score=80,
        failed_rules=("relative_volume",),
        watching=False,
        scanner_rank=2,
        technical_failed_rules=("relative_volume",),
    )
    publisher.publish(
        ScannerSnapshot(
            timestamp=NOW,
            active_symbols=("REJECTED", "NEAR", "INCOMPLETE"),
            decisions=(rejected, near_miss),
            ranked_candidates=(),
            processed_events=1,
            ignored_events=0,
            reference_failures=(),
        ),
        cycle=1,
        now=NOW,
    )

    rows = {
        event.symbol: dict(event.watchlist.metadata)
        for event in events
        if event.watchlist is not None and event.watchlist.subscribed is True
    }
    assert rows["REJECTED"]["scanner_classification"] == "INELIGIBLE"
    assert rows["REJECTED"]["scanner_failed_rules"] == "relative_volume, spread"
    assert "REJECTED" not in publisher._published_symbols
    assert "INCOMPLETE" not in rows


def test_all_view_retains_existing_classifications_and_is_bounded_to_25_rows():
    diagnostics = PerformanceDiagnostics()
    events = []
    publisher = ScannerSnapshotPublisher(
        events.append,
        lambda: 1,
        source="test",
        stale_after=timedelta(minutes=5),
        diagnostics=diagnostics,
    )
    decisions = tuple(
        decision(
            f"S{index:02d}",
            qualified=False,
            score=100 - index,
            failed_rules=("relative_volume", "spread"),
            scanner_rank=index + 1,
        )
        for index in range(30)
    )
    publisher.publish(
        ScannerSnapshot(
            timestamp=NOW,
            active_symbols=tuple(item.symbol for item in decisions),
            decisions=decisions,
            ranked_candidates=(),
            processed_events=1,
            ignored_events=0,
            reference_failures=(),
        ),
        cycle=1,
        now=NOW,
    )
    rows = {
        event.symbol for event in events
        if event.watchlist is not None and event.watchlist.subscribed is True
    }
    assert len(rows) == 25
    assert "S00" in rows and "S24" in rows and "S25" not in rows

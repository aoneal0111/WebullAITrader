from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json

from app.momentum_scanner.models import ScannerDecision, ScannerMetrics
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
    assert population["displayed_candidate_count"] == 1
    assert population["hidden_rejected_count"] == 1
    assert population["highest_hidden_rejected_score"] == 99
    assert population["hidden_ranked_ahead_by_displayed_candidate"] == {"WATCH": 1}
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
    assert population["hidden_rejected_count"] == 30
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

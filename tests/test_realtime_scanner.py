from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.momentum_scanner import (
    AssetClass,
    CatalystType,
    ScannerDecision,
    ScannerMetrics,
)
from app.realtime_scanner import RealtimeScannerEngine
from app.reference_data import ReferenceRecord
from app.universe import (
    SecurityType,
    UniverseSelection,
    UniverseSymbol,
)

D = Decimal


@dataclass(frozen=True)
class FakeEvent:
    symbol: str | None


class FakeUniverseService:
    def __init__(
        self,
        included: tuple[UniverseSymbol, ...],
    ) -> None:
        self.included = included

    def select_all(
        self,
        asset_classes: tuple[AssetClass, ...] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
    ) -> UniverseSelection:
        allowed = set(asset_classes)

        return UniverseSelection(
            included=tuple(
                item
                for item in self.included
                if item.asset_class in allowed
            ),
            excluded=(),
        )


class FakeReferenceService:
    def __init__(self) -> None:
        self.calls: list[
            tuple[str, AssetClass, bool]
        ] = []
        self.failures: set[str] = set()

    def get(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.STOCK,
        *,
        force_refresh: bool = False,
    ) -> ReferenceRecord:
        self.calls.append(
            (
                symbol,
                asset_class,
                force_refresh,
            )
        )

        if symbol in self.failures:
            raise LookupError(
                f"missing reference data for {symbol}"
            )

        return reference_record(
            symbol=symbol,
            asset_class=asset_class,
        )


class FakePipeline:
    def __init__(self) -> None:
        self.decisions: dict[
            str,
            ScannerDecision | None,
        ] = {}
        self.events: list[Any] = []

    def consume(
        self,
        event: Any,
    ) -> ScannerDecision | None:
        self.events.append(event)

        symbol = event.symbol.strip().upper()
        return self.decisions.get(symbol)


def universe_symbol(
    symbol: str,
    asset_class: AssetClass = AssetClass.STOCK,
) -> UniverseSymbol:
    return UniverseSymbol(
        symbol=symbol,
        asset_class=asset_class,
        exchange=(
            "CRYPTO"
            if asset_class is AssetClass.CRYPTO
            else "NASDAQ"
        ),
        security_type=(
            SecurityType.CRYPTO_PAIR
            if asset_class is AssetClass.CRYPTO
            else SecurityType.COMMON_STOCK
        ),
        tradable=True,
        price=(
            D("100000")
            if asset_class is AssetClass.CRYPTO
            else D("5")
        ),
        average_30_day_volume=D("1000000"),
        quote_currency="USD",
    )


def reference_record(
    symbol: str,
    asset_class: AssetClass,
) -> ReferenceRecord:
    return ReferenceRecord(
        symbol=symbol,
        asset_class=asset_class,
        exchange=(
            "CRYPTO"
            if asset_class is AssetClass.CRYPTO
            else "NASDAQ"
        ),
        previous_close=D("4"),
        average_30_day_volume=D("1000000"),
        float_shares=(
            None
            if asset_class is AssetClass.CRYPTO
            else D("8000000")
        ),
        market_cap=None,
        shares_outstanding=None,
        tradable=True,
        catalyst=CatalystType.EARNINGS,
        catalyst_headline="Test catalyst",
        as_of=datetime(
            2026,
            7,
            20,
            8,
            0,
            tzinfo=UTC,
        ),
    )


def scanner_decision(
    symbol: str,
    *,
    qualified: bool = True,
    score: int = 80,
    relative_volume: str = "6",
) -> ScannerDecision:
    metrics = ScannerMetrics(
        percentage_change=D("25"),
        relative_volume=D(relative_volume),
        dollar_volume=D("10000000"),
        spread_percent=D("0.25"),
    )

    return ScannerDecision(
        symbol=symbol,
        qualified=qualified,
        score=score,
        metrics=metrics,
        passed_rules=("price_range",),
        failed_rules=(
            ()
            if qualified
            else ("news_catalyst",)
        ),
    )


def make_engine(
    symbols: tuple[UniverseSymbol, ...],
) -> tuple[
    RealtimeScannerEngine,
    FakeReferenceService,
    FakePipeline,
    list[ReferenceRecord],
]:
    universe = FakeUniverseService(symbols)
    references = FakeReferenceService()
    pipeline = FakePipeline()
    stored_records: list[ReferenceRecord] = []

    engine = RealtimeScannerEngine(
        universe,
        references,
        pipeline,
        reference_sink=stored_records.append,
        clock=lambda: datetime(
            2026,
            7,
            20,
            14,
            0,
            tzinfo=UTC,
        ),
    )

    return (
        engine,
        references,
        pipeline,
        stored_records,
    )


def test_refresh_universe_warms_reference_data() -> None:
    engine, references, _, stored = make_engine(
        (
            universe_symbol("AAA"),
            universe_symbol(
                "BTCUSD",
                AssetClass.CRYPTO,
            ),
        )
    )

    active = engine.refresh_universe()

    assert active == ("AAA", "BTCUSD")
    assert len(references.calls) == 2
    assert tuple(
        item.symbol for item in stored
    ) == ("AAA", "BTCUSD")


def test_reference_failure_excludes_symbol() -> None:
    engine, references, _, _ = make_engine(
        (
            universe_symbol("AAA"),
            universe_symbol("BAD"),
        )
    )
    references.failures.add("BAD")

    active = engine.refresh_universe()
    snapshot = engine.snapshot()

    assert active == ("AAA",)
    assert len(snapshot.reference_failures) == 1
    assert (
        snapshot.reference_failures[0].symbol
        == "BAD"
    )


def test_inactive_symbol_event_is_ignored() -> None:
    engine, _, pipeline, _ = make_engine(
        (universe_symbol("AAA"),)
    )
    engine.refresh_universe()

    result = engine.consume(
        FakeEvent(symbol="OUTSIDE")
    )

    assert result is None
    assert pipeline.events == []
    assert engine.ignored_events == 1


def test_active_symbol_event_reaches_pipeline() -> None:
    engine, _, pipeline, _ = make_engine(
        (universe_symbol("AAA"),)
    )
    pipeline.decisions["AAA"] = scanner_decision(
        "AAA"
    )
    engine.refresh_universe()

    result = engine.consume(FakeEvent(symbol="aaa"))

    assert result is not None
    assert result.symbol == "AAA"
    assert engine.processed_events == 1
    assert len(pipeline.events) == 1


def test_ranked_candidates_exclude_failed_decisions() -> None:
    engine, _, pipeline, _ = make_engine(
        (
            universe_symbol("AAA"),
            universe_symbol("BBB"),
            universe_symbol("CCC"),
        )
    )

    pipeline.decisions = {
        "AAA": scanner_decision(
            "AAA",
            score=90,
        ),
        "BBB": scanner_decision(
            "BBB",
            qualified=False,
            score=99,
        ),
        "CCC": scanner_decision(
            "CCC",
            score=85,
        ),
    }

    engine.refresh_universe()
    engine.consume_many(
        (
            FakeEvent("AAA"),
            FakeEvent("BBB"),
            FakeEvent("CCC"),
        )
    )

    ranked = engine.ranked_candidates()

    assert tuple(
        item.symbol for item in ranked
    ) == ("AAA", "CCC")


def test_latest_decision_replaces_previous_decision() -> None:
    engine, _, pipeline, _ = make_engine(
        (universe_symbol("AAA"),)
    )
    engine.refresh_universe()

    pipeline.decisions["AAA"] = scanner_decision(
        "AAA",
        score=70,
    )
    engine.consume(FakeEvent("AAA"))

    pipeline.decisions["AAA"] = scanner_decision(
        "AAA",
        score=95,
    )
    engine.consume(FakeEvent("AAA"))

    snapshot = engine.snapshot()

    assert len(snapshot.decisions) == 1
    assert snapshot.decisions[0].score == 95


def test_removed_symbol_decision_is_deleted() -> None:
    universe = FakeUniverseService(
        (
            universe_symbol("AAA"),
            universe_symbol("BBB"),
        )
    )
    references = FakeReferenceService()
    pipeline = FakePipeline()

    engine = RealtimeScannerEngine(
        universe,
        references,
        pipeline,
    )

    pipeline.decisions["AAA"] = scanner_decision(
        "AAA"
    )

    engine.refresh_universe()
    engine.consume(FakeEvent("AAA"))

    universe.included = (
        universe_symbol("BBB"),
    )
    engine.refresh_universe()

    snapshot = engine.snapshot()

    assert snapshot.active_symbols == ("BBB",)
    assert snapshot.decisions == ()


def test_force_refresh_is_forwarded() -> None:
    engine, references, _, _ = make_engine(
        (universe_symbol("AAA"),)
    )

    engine.refresh_universe(
        force_reference_refresh=True
    )

    assert references.calls == [
        ("AAA", AssetClass.STOCK, True)
    ]


def test_snapshot_contains_runtime_counts() -> None:
    engine, _, pipeline, _ = make_engine(
        (universe_symbol("AAA"),)
    )
    pipeline.decisions["AAA"] = scanner_decision(
        "AAA"
    )

    engine.refresh_universe()
    engine.consume(FakeEvent("AAA"))
    engine.consume(FakeEvent("OUTSIDE"))

    snapshot = engine.snapshot()

    assert snapshot.processed_events == 1
    assert snapshot.ignored_events == 1
    assert snapshot.qualified_count == 1
    assert snapshot.decision_count == 1


def test_engine_has_no_execution_methods() -> None:
    method_names = set(
        RealtimeScannerEngine.__dict__
    )

    assert "submit_order" not in method_names
    assert "place_order" not in method_names
    assert "cancel_order" not in method_names



def test_reset_stream_state_delegates_active_symbols_and_clears_decisions() -> None:
    class ResettablePipeline:
        def __init__(self):
            self.reset_calls = []

        def consume(self, event):
            return scanner_decision(event.symbol)

        def reset_symbol(self, symbol):
            self.reset_calls.append(symbol)

    pipeline = ResettablePipeline()
    universe = FakeUniverseService(
        (
            universe_symbol("AAA"),
            universe_symbol("BBB"),
        )
    )

    engine = RealtimeScannerEngine(
        universe,
        FakeReferenceService(),
        pipeline,
        clock=lambda: datetime(
            2026,
            7,
            20,
            14,
            0,
            tzinfo=UTC,
        ),
    )

    engine.refresh_universe()
    engine.consume(FakeEvent("AAA"))
    engine.consume(FakeEvent("BBB"))

    before = engine.snapshot()

    assert tuple(item.symbol for item in before.decisions) == (
        "AAA",
        "BBB",
    )

    active = engine.active_symbols
    reset = engine.reset_stream_state()

    after = engine.snapshot()

    assert reset == active
    assert tuple(pipeline.reset_calls) == active
    assert after.decisions == ()
    assert after.active_symbols == active

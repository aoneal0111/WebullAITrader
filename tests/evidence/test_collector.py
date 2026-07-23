from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

import pytest

from app.evidence import (
    Evidence,
    EvidenceCategory,
    EvidenceCollectionError,
    EvidenceCollector,
    EvidenceValidationError,
    SignalDirection,
)


@dataclass(frozen=True)
class Snapshot:
    symbol: str


class GoodProvider:
    @property
    def name(self) -> str:
        return "RSI"

    def generate(
        self,
        snapshot: Snapshot,
    ) -> Sequence[Evidence]:
        return (
            Evidence(
                symbol=snapshot.symbol,
                timestamp=datetime.now(timezone.utc),
                source=self.name,
                category=EvidenceCategory.TECHNICAL,
                direction=SignalDirection.LONG,
                confidence=0.8,
                strength=0.7,
                explanation="RSI recovered from oversold.",
            ),
        )


class EmptyProvider:
    @property
    def name(self) -> str:
        return "EMPTY"

    def generate(
        self,
        snapshot: Snapshot,
    ) -> Sequence[Evidence]:
        return ()


class FailingProvider:
    @property
    def name(self) -> str:
        return "FAIL"

    def generate(
        self,
        snapshot: Snapshot,
    ) -> Sequence[Evidence]:
        raise RuntimeError("provider failure")


class WrongSourceProvider:
    @property
    def name(self) -> str:
        return "EXPECTED"

    def generate(
        self,
        snapshot: Snapshot,
    ) -> Sequence[Evidence]:
        return (
            Evidence(
                symbol=snapshot.symbol,
                timestamp=datetime.now(timezone.utc),
                source="WRONG",
                category=EvidenceCategory.TECHNICAL,
                direction=SignalDirection.NEUTRAL,
                confidence=0.5,
                strength=0.5,
                explanation="Test evidence.",
            ),
        )


def test_collector_collects_provider_evidence() -> None:
    collector = EvidenceCollector(
        [GoodProvider(), EmptyProvider()]
    )

    result = collector.collect(Snapshot(symbol="AAPL"))

    assert len(result.evidence) == 1
    assert result.evidence[0].symbol == "AAPL"
    assert result.provider_names == ("RSI", "EMPTY")


def test_collection_filters_by_symbol() -> None:
    collector = EvidenceCollector([GoodProvider()])
    result = collector.collect(Snapshot(symbol="AAPL"))

    assert len(result.for_symbol("aapl")) == 1
    assert result.for_symbol("MSFT") == ()


def test_duplicate_provider_names_are_rejected() -> None:
    with pytest.raises(
        EvidenceValidationError,
        match="must be unique",
    ):
        EvidenceCollector([GoodProvider(), GoodProvider()])


def test_provider_failure_is_wrapped() -> None:
    collector = EvidenceCollector([FailingProvider()])

    with pytest.raises(
        EvidenceCollectionError,
        match="Evidence provider failed",
    ):
        collector.collect(Snapshot(symbol="AAPL"))


def test_source_must_match_provider_name() -> None:
    collector = EvidenceCollector([WrongSourceProvider()])

    with pytest.raises(
        EvidenceCollectionError,
        match="does not match provider name",
    ):
        collector.collect(Snapshot(symbol="AAPL"))

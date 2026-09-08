from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.assets import AssetType
from app.crypto_research import (
    CryptoBarInterval,
    CryptoDiscoveryContext,
    CryptoDiscoveryEngine,
    CryptoPair,
    CryptoRegimeLabel,
    CryptoStrategyState,
    active_crypto_strategies,
    benchmark_evidence,
    breadth_evidence,
    correlation_evidence,
    dispersion_evidence,
    relative_strength_evidence,
    regime_evidence,
    crypto_strategy_taxonomy,
)
from app.crypto_research.evidence import CryptoCompletedBar, CryptoEvidenceContext


CUTOFF = datetime(2026, 9, 8, 12, tzinfo=UTC)
PAIR = CryptoPair.configured("SOL/USD")
BTC = CryptoPair.configured("BTC/USD")
ETH = CryptoPair.configured("ETH/USD")


def bars(pair: CryptoPair, closes: list[str]) -> tuple[CryptoCompletedBar, ...]:
    result = []
    start = CUTOFF - timedelta(minutes=5 * len(closes))
    for index, close in enumerate(closes):
        opened = start + timedelta(minutes=5 * index)
        value = Decimal(close)
        result.append(CryptoCompletedBar(AssetType.CRYPTO, pair.canonical_symbol, pair.provider_symbol, CryptoBarInterval.M5, opened, opened + timedelta(minutes=5), value - Decimal("1"), value + Decimal("1"), value - Decimal("2"), value, None, "fixture", opened + timedelta(minutes=5), CUTOFF))
    return tuple(result)


def context(closes: list[str], *, rs: bool = True, cutoff: datetime = CUTOFF) -> CryptoDiscoveryContext:
    subject = bars(PAIR, closes)
    btc_bars = bars(BTC, ["100", "101", "102", "103", "104", "105", "106", "107", "108", "109"])
    eth_bars = bars(ETH, ["100", "101", "102", "103", "104", "105", "106", "107", "108", "109"])
    btc_e = benchmark_evidence(BTC, btc_bars, interval=CryptoBarInterval.M5, decision_cutoff=cutoff, window_bars=10)
    eth_e = benchmark_evidence(ETH, eth_bars, interval=CryptoBarInterval.M5, decision_cutoff=cutoff, window_bars=10)
    series = {"SOL/USD": subject, "BTC/USD": btc_bars, "ETH/USD": eth_bars}
    breadth = breadth_evidence(series, interval=CryptoBarInterval.M5, decision_cutoff=cutoff, observed_universe_size=3)
    dispersion = dispersion_evidence(breadth)
    rs_btc = relative_strength_evidence(subject, btc_bars, canonical_symbol="SOL/USD", benchmark_symbol="BTC/USD", interval=CryptoBarInterval.M5, decision_cutoff=cutoff)
    rs_eth = relative_strength_evidence(subject, eth_bars, canonical_symbol="SOL/USD", benchmark_symbol="ETH/USD", interval=CryptoBarInterval.M5, decision_cutoff=cutoff)
    if not rs:
        rs_btc = type(rs_btc)(**{**rs_btc.__dict__, "excess_return_percent": None}) if hasattr(rs_btc, "__dict__") else rs_btc
    regime = regime_evidence(btc_e, eth_e, breadth, dispersion, at=cutoff)
    evidence = CryptoEvidenceContext("SOL/USD", "SOLUSD", cutoff, regime.window, ((CryptoBarInterval.M5, subject),), btc_e, eth_e, breadth, dispersion, correlation_evidence(subject, btc_bars, canonical_symbol="SOL/USD", benchmark_symbol="BTC/USD", interval=CryptoBarInterval.M5, decision_cutoff=cutoff), correlation_evidence(subject, eth_bars, canonical_symbol="SOL/USD", benchmark_symbol="ETH/USD", interval=CryptoBarInterval.M5, decision_cutoff=cutoff), rs_btc, rs_eth, regime)
    return CryptoDiscoveryContext(evidence)


def test_taxonomy_is_explicit_and_separate_from_equity() -> None:
    taxonomy = crypto_strategy_taxonomy()
    assert len(taxonomy) == 34
    assert len(active_crypto_strategies()) == 8
    assert all(item.research_only and not item.selection_authorized and not item.execution_authorized for item in taxonomy)
    assert all(item.state is not CryptoStrategyState.ACTIVE for item in taxonomy if item.strategy_id in {"CRYPTO_CATALYST_CONTINUATION", "LIQUIDITY_SWEEP_RECLAIM", "DEPTH_PRESSURE_CONTINUATION", "ORDER_BOOK_IMBALANCE_CONTINUATION"})


def test_bar_strategies_emit_shared_research_detections_and_geometry() -> None:
    result = CryptoDiscoveryEngine().evaluate((context(["100", "102", "104", "103", "102", "106", "108", "110"]),))
    assert result.detections
    assert all(item.identity.asset_type is AssetType.CRYPTO for item in result.detections)
    assert all(item.detection.research_only and item.risk_geometry is not None for item in result.detections)
    assert all(item.identity.canonical_symbol == "SOL/USD" for item in result.detections)


def test_relative_strength_and_breadth_strategies_can_overlap() -> None:
    result = CryptoDiscoveryEngine().evaluate((context(["100", "101", "102", "103", "104", "105", "106", "107"]),))
    ids = {item.strategy_id for item in result.detections}
    assert "BTC_ETH_RELATIVE_STRENGTH_LEADERSHIP" in ids
    assert "BREADTH_ALIGNED_TREND_CONTINUATION" in ids
    assert result.opportunities
    assert any(len(item.memberships) > 1 for item in result.opportunities)


def test_negative_and_insufficient_contexts_do_not_emit_authorized_opportunities() -> None:
    engine = CryptoDiscoveryEngine()
    negative = engine.evaluate((context(["100", "99", "98", "97", "96", "95", "94", "93"]),))
    insufficient = engine.evaluate((context(["100", "101"]),))
    assert not negative.opportunities or all(item.research_only for item in negative.opportunities)
    assert not insufficient.detections


def test_future_evidence_is_rejected_before_discovery() -> None:
    with pytest.raises(ValueError):
        bars(PAIR, ["100"])[0].__class__(AssetType.CRYPTO, "SOL/USD", "SOLUSD", CryptoBarInterval.M5, CUTOFF, CUTOFF + timedelta(minutes=5), Decimal("99"), Decimal("101"), Decimal("98"), Decimal("100"), None, "fixture", CUTOFF, CUTOFF)


def test_registry_and_failure_isolation_are_bounded() -> None:
    engine = CryptoDiscoveryEngine(maximum_opportunities=4096, maximum_signatures=16384, maximum_episodes=8192)
    assert len(engine.registry.detectors) == len(active_crypto_strategies())
    assert engine.evaluate((context(["100", "101"]),)).metrics.crypto_strategy_symbols == 1

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.assets import AssetType
from app.crypto_research import (
    CryptoBarInterval,
    CryptoCatalystCollectionConfig,
    CryptoCatalystCollectionSink,
    CryptoDiscoveryContext,
    CryptoDiscoveryEngine,
    CryptoEvidenceContext,
    CryptoIntelligenceResearchRuntime,
    CryptoPair,
    CryptoCompletedBar,
    benchmark_evidence,
    breadth_evidence,
    correlation_evidence,
    dispersion_evidence,
    relative_strength_evidence,
    regime_evidence,
)


CUTOFF = datetime(2026, 9, 8, 12, tzinfo=UTC)
PAIR = CryptoPair.configured("SOL/USD")
BTC = CryptoPair.configured("BTC/USD")
ETH = CryptoPair.configured("ETH/USD")


def _bars(pair: CryptoPair, closes: list[str], *, start: datetime | None = None, decision_cutoff: datetime = CUTOFF) -> tuple[CryptoCompletedBar, ...]:
    start = start or CUTOFF - timedelta(minutes=5 * len(closes))
    result = []
    for index, close in enumerate(closes):
        opened = start + timedelta(minutes=5 * index)
        value = Decimal(close)
        result.append(CryptoCompletedBar(
            AssetType.CRYPTO, pair.canonical_symbol, pair.provider_symbol,
            CryptoBarInterval.M5, opened, opened + timedelta(minutes=5),
            value - 1, value + 1, value - 2, value, None, "fixture",
            opened + timedelta(minutes=5), decision_cutoff,
        ))
    return tuple(result)


def _context() -> CryptoDiscoveryContext:
    subject = _bars(PAIR, ["100", "102", "104", "103", "102", "106", "108", "110"])
    btc = _bars(BTC, ["100", "101", "102", "103", "104", "105", "106", "107", "108", "109"])
    eth = _bars(ETH, ["100", "101", "102", "103", "104", "105", "106", "107", "108", "109"])
    btc_e = benchmark_evidence(BTC, btc, interval=CryptoBarInterval.M5, decision_cutoff=CUTOFF, window_bars=10)
    eth_e = benchmark_evidence(ETH, eth, interval=CryptoBarInterval.M5, decision_cutoff=CUTOFF, window_bars=10)
    series = {"SOL/USD": subject, "BTC/USD": btc, "ETH/USD": eth}
    breadth = breadth_evidence(series, interval=CryptoBarInterval.M5, decision_cutoff=CUTOFF, observed_universe_size=3)
    dispersion = dispersion_evidence(breadth)
    return CryptoDiscoveryContext(CryptoEvidenceContext(
        "SOL/USD", "SOLUSD", CUTOFF, regime_evidence(btc_e, eth_e, breadth, dispersion, at=CUTOFF).window,
        ((CryptoBarInterval.M5, subject),), btc_e, eth_e, breadth, dispersion,
        correlation_evidence(subject, btc, canonical_symbol="SOL/USD", benchmark_symbol="BTC/USD", interval=CryptoBarInterval.M5, decision_cutoff=CUTOFF),
        correlation_evidence(subject, eth, canonical_symbol="SOL/USD", benchmark_symbol="ETH/USD", interval=CryptoBarInterval.M5, decision_cutoff=CUTOFF),
        relative_strength_evidence(subject, btc, canonical_symbol="SOL/USD", benchmark_symbol="BTC/USD", interval=CryptoBarInterval.M5, decision_cutoff=CUTOFF),
        relative_strength_evidence(subject, eth, canonical_symbol="SOL/USD", benchmark_symbol="ETH/USD", interval=CryptoBarInterval.M5, decision_cutoff=CUTOFF),
        regime_evidence(btc_e, eth_e, breadth, dispersion, at=CUTOFF),
    ))


def test_disabled_runtime_is_a_noop() -> None:
    runtime = CryptoIntelligenceResearchRuntime(enabled=False)
    assert runtime.start() is False
    assert runtime.submit_context(_context()) == ()
    assert runtime.metrics().contexts_seen == 0


def test_material_decision_is_frozen_and_duplicate_context_is_suppressed(tmp_path) -> None:
    path = tmp_path / "collection.jsonl"
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=path))
    runtime = CryptoIntelligenceResearchRuntime(
        enabled=True, discovery=CryptoDiscoveryEngine(), collection=sink,
    )
    runtime.start()
    first = runtime.submit_context(_context())
    second = runtime.submit_context(_context())
    runtime.close()
    assert first
    assert second == ()
    assert runtime.metrics().decisions_registered == len(first)
    assert runtime.metrics().decision_duplicates >= 1


def test_four_horizons_use_the_original_decision_linkage(tmp_path) -> None:
    path = tmp_path / "collection.jsonl"
    sink = CryptoCatalystCollectionSink(CryptoCatalystCollectionConfig(enabled=True, path=path))
    runtime = CryptoIntelligenceResearchRuntime(enabled=True, collection=sink)
    runtime.start()
    decisions = runtime.submit_context(_context())
    assert decisions
    decision = decisions[0]
    future = _bars(PAIR, ["110", "111", "112", "113", "114", "115", "116", "117", "118"], start=CUTOFF, decision_cutoff=CUTOFF + timedelta(hours=2))
    emitted = runtime.update_outcomes(decision.identity.deterministic_id, future)
    runtime.close()
    assert len(emitted) == 4
    assert runtime.metrics().active_decisions == len(decisions) - 1
    lines = path.read_text(encoding="utf-8").splitlines()
    observations = [line for line in lines if '"record_type":"OUTCOME_OBSERVATION"' in line]
    assert len(observations) == 4


def test_missing_catalyst_view_is_unavailable_not_no_evidence() -> None:
    runtime = CryptoIntelligenceResearchRuntime(enabled=True)
    runtime.start()
    decisions = runtime.submit_context(_context())
    assert decisions
    link = runtime._links[decisions[0].identity.deterministic_id]
    assert link.snapshot.summary.overall_evidence_state.value == "UNAVAILABLE"
    runtime.close()


def test_desktop_composition_owns_the_intelligence_lifecycle(monkeypatch, tmp_path) -> None:
    from app.composition import desktop as desktop_module
    from app.configuration import load_configuration

    configuration = load_configuration({
        "CRYPTO_INTELLIGENCE_RESEARCH_ENABLED": "true",
        "CRYPTO_DISCOVERY_ENABLED": "false",
        "ATLAS_MEMORY_OBSERVABILITY_ENABLED": "false",
        "TRADE_INTELLIGENCE_ENABLED": "false",
    })
    monkeypatch.setattr(desktop_module, "load_configuration", lambda: configuration)
    composition = desktop_module.create_desktop_composition(
        paper_persistence_path=tmp_path / "paper.sqlite3",
    )
    try:
        assert composition.crypto_intelligence_runtime is not None
        assert composition.crypto_intelligence_runtime.enabled is True
    finally:
        composition.close()

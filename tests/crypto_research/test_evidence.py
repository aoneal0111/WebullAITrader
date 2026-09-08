from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from dataclasses import fields

import pytest

from app.assets import AssetType
from app.crypto_research import (
    BoundedCryptoBarHistory,
    CryptoBarInterval,
    CryptoEvidenceLedger,
    CryptoCompletedBar,
    CryptoEvidenceContext,
    CryptoHistoryRefreshPlanner,
    CryptoPair,
    CryptoRegimeLabel,
    FutureCryptoEvidenceError,
    MalformedCryptoBarError,
    SUPPORTED_CRYPTO_BAR_INTERVALS,
    benchmark_evidence,
    breadth_evidence,
    build_crypto_evidence_context,
    correlation_evidence,
    dispersion_evidence,
    normalize_crypto_bar,
    normalize_crypto_rows,
    regime_evidence,
    relative_strength_evidence,
)
from app.crypto_research.models import CryptoResearchRegime, crypto_regime


T0 = datetime(2026, 9, 7, 13, 0, tzinfo=UTC)
PAIR = CryptoPair.configured("BTC/USD")
ALT = CryptoPair.configured("SOL/USD")


def row(index: int, *, close: str = "100", volume: str | None = "10", **extra):
    opened = T0 + timedelta(minutes=index)
    value = Decimal(close)
    payload = {
        "timestamp": int(opened.timestamp() * 1000),
        "open": str(value - Decimal("1")),
        "high": str(value + Decimal("1")),
        "low": str(value - Decimal("2")),
        "close": close,
    }
    if volume is not None:
        payload["volume"] = volume
    payload.update(extra)
    return payload


def bar(pair: CryptoPair, index: int, close: str) -> CryptoCompletedBar:
    opened = T0 + timedelta(minutes=index)
    value = Decimal(close)
    return CryptoCompletedBar(
        AssetType.CRYPTO,
        pair.canonical_symbol,
        pair.provider_symbol,
        CryptoBarInterval.M1,
        opened,
        opened + timedelta(minutes=1),
        value - Decimal("1"),
        value + Decimal("1"),
        value - Decimal("2"),
        value,
        Decimal("10"),
        "TEST",
        T0 + timedelta(days=1),
        T0 + timedelta(days=1),
    )


def bars(pair: CryptoPair, closes: list[str]) -> tuple[CryptoCompletedBar, ...]:
    return tuple(bar(pair, index, close) for index, close in enumerate(closes))


def test_installed_sdk_intervals_are_explicit_and_phase_b_uses_three():
    assert tuple(item.value for item in SUPPORTED_CRYPTO_BAR_INTERVALS) == ("M1", "M5", "M15")
    assert CryptoBarInterval.M1.duration == timedelta(minutes=1)
    assert CryptoBarInterval.M5.duration == timedelta(minutes=5)
    assert CryptoBarInterval.M15.duration == timedelta(minutes=15)


def test_webull_row_normalization_preserves_pair_ohlc_and_missing_volume():
    cutoff = T0 + timedelta(minutes=10)
    normalized = normalize_crypto_bar(
        PAIR,
        row(0, close="101", volume=None),
        interval=CryptoBarInterval.M1,
        observed_at=cutoff,
        decision_cutoff=cutoff,
    )
    assert normalized is not None
    assert normalized.asset_type is AssetType.CRYPTO
    assert normalized.canonical_symbol == "BTC/USD"
    assert normalized.provider_symbol == "BTCUSD"
    assert normalized.open == Decimal("100")
    assert normalized.high == Decimal("102")
    assert normalized.low == Decimal("99")
    assert normalized.close == Decimal("101")
    assert normalized.volume is None
    assert normalized.closed_at == normalized.opened_at + timedelta(minutes=1)
    assert normalized.is_complete


def test_batch_normalizer_contains_malformed_incomplete_and_duplicate_rows():
    cutoff = T0 + timedelta(minutes=10)
    result = normalize_crypto_rows(
        PAIR,
        [row(0), row(0), row(1, is_complete=False), {"timestamp": "bad"}],
        interval=CryptoBarInterval.M1,
        observed_at=cutoff,
        decision_cutoff=cutoff,
    )
    assert len(result.bars) == 1
    assert result.duplicate_rows == 1
    assert result.incomplete_rows == 1
    assert result.malformed_rows == 1


def test_incomplete_and_future_rows_are_excluded_without_becoming_completed_evidence():
    cutoff = T0 + timedelta(minutes=2)
    assert normalize_crypto_bar(
        PAIR,
        row(0, is_complete=False),
        interval=CryptoBarInterval.M1,
        observed_at=cutoff,
        decision_cutoff=cutoff,
    ) is None
    assert normalize_crypto_bar(
        PAIR,
        row(2),
        interval=CryptoBarInterval.M1,
        observed_at=cutoff,
        decision_cutoff=cutoff,
    ) is None
    with pytest.raises(MalformedCryptoBarError):
        normalize_crypto_bar(
            PAIR,
            {"timestamp": int(T0.timestamp() * 1000), "open": "bad"},
            interval=CryptoBarInterval.M1,
            observed_at=cutoff,
            decision_cutoff=cutoff,
        )


def test_history_is_ordered_deduplicated_and_bounded():
    history = BoundedCryptoBarHistory(maximum_symbols=256, bars_per_series=64)
    for index in range(70, -1, -1):
        assert history.add(bar(PAIR, index, str(100 + index)))
    assert not history.add(bar(PAIR, 70, "170"))
    retained = history.bars("BTC/USD", CryptoBarInterval.M1)
    assert len(retained) == 64
    assert retained[0].opened_at < retained[-1].opened_at
    metrics = history.metrics()
    assert metrics.crypto_bar_symbols == 1
    assert metrics.crypto_bar_series == 1
    assert metrics.crypto_completed_bars_retained == 64
    assert metrics.crypto_evidence_duplicates_suppressed == 1


def test_history_supports_256_symbols_and_three_bounded_series():
    history = BoundedCryptoBarHistory()
    for index in range(256):
        pair = CryptoPair.configured(f"C{index:03d}/USD")
        for interval in SUPPORTED_CRYPTO_BAR_INTERVALS:
            opened = T0 + timedelta(minutes=index)
            unit = CryptoCompletedBar(
                AssetType.CRYPTO, pair.canonical_symbol, pair.provider_symbol,
                interval, opened, opened + interval.duration,
                Decimal("99"), Decimal("101"), Decimal("98"), Decimal("100"),
                None, "TEST", T0 + timedelta(days=1), T0 + timedelta(days=1),
            )
            assert history.add(unit)
    metrics = history.metrics()
    assert metrics.crypto_bar_symbols == 256
    assert metrics.crypto_bar_series == 768
    assert metrics.crypto_completed_bars_retained == 768


def test_regime_windows_are_utc_analytic_windows_not_exchange_authority():
    assert crypto_regime(datetime(2026, 9, 4, 6, 59, tzinfo=UTC)) is CryptoResearchRegime.ASIA
    assert crypto_regime(datetime(2026, 9, 4, 7, 0, tzinfo=UTC)) is CryptoResearchRegime.EUROPE
    assert crypto_regime(datetime(2026, 9, 4, 12, 59, tzinfo=UTC)) is CryptoResearchRegime.EUROPE
    assert crypto_regime(datetime(2026, 9, 4, 13, 0, tzinfo=UTC)) is CryptoResearchRegime.US
    assert crypto_regime(datetime(2026, 9, 5, 13, 0, tzinfo=UTC)) is CryptoResearchRegime.WEEKEND


def test_btc_and_eth_benchmark_evidence_is_point_in_time_and_transparent():
    cutoff = T0 + timedelta(minutes=30)
    btc = benchmark_evidence(PAIR, bars(PAIR, [str(100 + index) for index in range(21)]), interval=CryptoBarInterval.M1, decision_cutoff=cutoff)
    eth_pair = CryptoPair.configured("ETH/USD")
    eth = benchmark_evidence(eth_pair, bars(eth_pair, [str(100 + index * 2) for index in range(21)]), interval=CryptoBarInterval.M1, decision_cutoff=cutoff)
    assert btc.return_percent is not None and btc.return_percent > 0
    assert eth.return_percent is not None and eth.return_percent > btc.return_percent
    assert btc.volatility is not None
    assert btc.momentum_velocity is not None
    assert btc.high_low_location is not None
    assert all(timestamp <= cutoff for timestamp in btc.source_timestamps)
    assert btc.provenance.decision_cutoff == cutoff


def test_breadth_excludes_unavailable_symbols_and_reports_coverage_median():
    cutoff = T0 + timedelta(minutes=10)
    series = {
        "BTC/USD": bars(PAIR, ["100", "110"]),
        "ETH/USD": bars(CryptoPair.configured("ETH/USD"), ["100", "90"]),
        "SOL/USD": bars(ALT, ["100", "100"]),
        "ADA/USD": bars(CryptoPair.configured("ADA/USD"), ["100", "105"]),
        "MISSING/USD": (),
    }
    breadth = breadth_evidence(series, interval=CryptoBarInterval.M1, decision_cutoff=cutoff, observed_universe_size=5)
    assert breadth.eligible_sample_size == 4
    assert breadth.observed_universe_size == 5
    assert breadth.coverage_percent == Decimal("80")
    assert (breadth.advancing_count, breadth.declining_count, breadth.unchanged_count) == (2, 1, 1)
    assert breadth.median_return_percent == Decimal("2.50")
    assert breadth.advancing_percent == Decimal("50")
    assert breadth.negative_return_percent == Decimal("25")
    dispersion = dispersion_evidence(breadth)
    assert dispersion.standard_deviation is not None
    assert dispersion.median_absolute_deviation is not None


def test_aligned_correlation_requires_minimum_observations_and_never_forward_fills():
    cutoff = T0 + timedelta(minutes=40)
    left = bars(ALT, [str(100 + index) for index in range(22)])
    benchmark = bars(PAIR, [str(200 + index * 2) for index in range(22)])
    complete = correlation_evidence(left, benchmark, canonical_symbol="SOL/USD", benchmark_symbol="BTC/USD", interval=CryptoBarInterval.M1, decision_cutoff=cutoff, window_bars=20, minimum_observations=10)
    assert complete.observation_count == 20
    assert complete.correlation == Decimal("1")
    sparse = correlation_evidence(left[::3], benchmark, canonical_symbol="SOL/USD", benchmark_symbol="BTC/USD", interval=CryptoBarInterval.M1, decision_cutoff=cutoff, window_bars=20, minimum_observations=10)
    assert sparse.observation_count < 10
    assert sparse.correlation is None


def test_relative_strength_is_excess_return_vs_btc_or_eth():
    cutoff = T0 + timedelta(minutes=20)
    symbol = bars(ALT, ["100", "101", "102", "103", "104", "105"])
    benchmark = bars(PAIR, ["100", "100", "100", "100", "100", "100"])
    evidence = relative_strength_evidence(symbol, benchmark, canonical_symbol="SOL/USD", benchmark_symbol="BTC/USD", interval=CryptoBarInterval.M1, decision_cutoff=cutoff, horizon_bars=5)
    assert evidence.symbol_return_percent == Decimal("5")
    assert evidence.benchmark_return_percent == Decimal("0")
    assert evidence.excess_return_percent == Decimal("5")


def test_regime_labels_are_deterministic_and_missing_evidence_is_honest():
    cutoff = T0 + timedelta(minutes=30)
    btc = benchmark_evidence(PAIR, bars(PAIR, [str(100 + index) for index in range(21)]), interval=CryptoBarInterval.M1, decision_cutoff=cutoff)
    eth_pair = CryptoPair.configured("ETH/USD")
    eth = benchmark_evidence(eth_pair, bars(eth_pair, [str(100 + index) for index in range(21)]), interval=CryptoBarInterval.M1, decision_cutoff=cutoff)
    breadth = breadth_evidence({"BTC/USD": bars(PAIR, ["100", "110"]), "ETH/USD": bars(eth_pair, ["100", "110"])}, interval=CryptoBarInterval.M1, decision_cutoff=cutoff, observed_universe_size=2)
    dispersion = dispersion_evidence(breadth)
    regime = regime_evidence(btc, eth, breadth, dispersion, at=cutoff)
    assert regime.label is CryptoRegimeLabel.BROAD_RISK_ON
    insufficient_breadth = breadth_evidence({}, interval=CryptoBarInterval.M1, decision_cutoff=cutoff, observed_universe_size=2)
    insufficient = regime_evidence(btc, eth, insufficient_breadth, dispersion_evidence(insufficient_breadth), at=cutoff)
    assert insufficient.label is CryptoRegimeLabel.INSUFFICIENT_EVIDENCE


def test_refresh_planner_stages_one_interval_per_cadence():
    pairs = tuple(CryptoPair.configured(f"C{index:03d}/USD") for index in range(256))
    planner = CryptoHistoryRefreshPlanner(pairs)
    assert planner.requests_per_cycle == 13
    assert planner.requests_per_second == Decimal("13") / Decimal("60")
    assert all(len(request.symbols) <= 20 for request in planner.plan(0))
    assert planner.plan(0)[0].interval is CryptoBarInterval.M1
    assert planner.plan(0)[0].symbols[0].canonical_symbol == "C000/USD"
    assert planner.plan(1)[0].interval is CryptoBarInterval.M5
    assert planner.plan(2)[0].interval is CryptoBarInterval.M15
    assert planner.plan(3)[0].interval is CryptoBarInterval.M1

    prioritized = CryptoHistoryRefreshPlanner(
        (CryptoPair.configured("SOL/USD"), CryptoPair.configured("ETH/USD"), CryptoPair.configured("BTC/USD"))
    )
    assert tuple(item.canonical_symbol for item in prioritized.plan(0)[0].symbols[:2]) == ("BTC/USD", "ETH/USD")


def test_evidence_ledger_exposes_only_bounded_scalar_metrics():
    ledger = CryptoEvidenceLedger()
    ledger.add_bars((bar(PAIR, 0, "100"),))
    ledger.record_benchmark()
    ledger.record_regime()
    ledger.record_breadth(12)
    ledger.record_relative_strength(11)
    ledger.record_failure()
    metrics = ledger.metrics()
    assert metrics.crypto_bar_symbols == 1
    assert metrics.crypto_completed_bars_retained == 1
    assert metrics.crypto_benchmark_updates == 1
    assert metrics.crypto_regime_updates == 1
    assert metrics.crypto_breadth_sample_size == 12
    assert metrics.crypto_relative_strength_symbols == 11
    assert metrics.crypto_evidence_failures == 1
    assert not any(isinstance(value, (dict, list, set)) for value in fields(metrics))


def test_context_is_research_only_and_has_no_equity_or_execution_fields():
    cutoff = T0 + timedelta(minutes=30)
    btc = benchmark_evidence(PAIR, bars(PAIR, [str(100 + index) for index in range(21)]), interval=CryptoBarInterval.M1, decision_cutoff=cutoff)
    eth_pair = CryptoPair.configured("ETH/USD")
    eth = benchmark_evidence(eth_pair, bars(eth_pair, [str(100 + index) for index in range(21)]), interval=CryptoBarInterval.M1, decision_cutoff=cutoff)
    breadth = breadth_evidence({"BTC/USD": bars(PAIR, ["100", "101"]), "ETH/USD": bars(eth_pair, ["100", "101"])}, interval=CryptoBarInterval.M1, decision_cutoff=cutoff, observed_universe_size=2)
    dispersion = dispersion_evidence(breadth)
    context = CryptoEvidenceContext("SOL/USD", "SOLUSD", cutoff, CryptoResearchRegime.US, ((CryptoBarInterval.M1, bars(ALT, ["100", "101"])),), btc, eth, breadth, dispersion, correlation_evidence((), (), canonical_symbol="SOL/USD", benchmark_symbol="BTC/USD", interval=CryptoBarInterval.M1, decision_cutoff=cutoff), correlation_evidence((), (), canonical_symbol="SOL/USD", benchmark_symbol="ETH/USD", interval=CryptoBarInterval.M1, decision_cutoff=cutoff), relative_strength_evidence((), (), canonical_symbol="SOL/USD", benchmark_symbol="BTC/USD", interval=CryptoBarInterval.M1, decision_cutoff=cutoff), relative_strength_evidence((), (), canonical_symbol="SOL/USD", benchmark_symbol="ETH/USD", interval=CryptoBarInterval.M1, decision_cutoff=cutoff), regime_evidence(btc, eth, breadth, dispersion, at=cutoff))
    assert context.research_only
    names = {item.name for item in fields(CryptoEvidenceContext)}
    assert not names & {"float", "premarket", "session_date", "quantity", "order_id", "paper", "live"}


def test_context_builder_composes_bounded_history_without_runtime_wiring():
    cutoff = T0 + timedelta(days=1)
    history = BoundedCryptoBarHistory()
    eth = CryptoPair.configured("ETH/USD")
    history.add_many(
        tuple(bar(pair, index, str(100 + index)) for pair in (PAIR, eth, ALT) for index in range(21))
    )
    context = build_crypto_evidence_context(
        ALT, history, decision_cutoff=cutoff, observed_universe_size=3
    )
    assert context.canonical_symbol == "SOL/USD"
    assert context.btc_benchmark.canonical_symbol == "BTC/USD"
    assert context.eth_benchmark.canonical_symbol == "ETH/USD"
    assert context.breadth.eligible_sample_size == 3
    assert context.research_only

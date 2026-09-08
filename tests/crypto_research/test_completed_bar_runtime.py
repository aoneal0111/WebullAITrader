from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.crypto_research import CryptoPair, CryptoResearchRuntime


T0 = datetime(2026, 9, 8, 12, tzinfo=UTC)


def _rows(interval_minutes: int) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "time": int((T0 - timedelta(minutes=interval_minutes * (index + 1))).timestamp() * 1000),
            "open": str(100 + index),
            "high": str(101 + index),
            "low": str(99 + index),
            "close": str(100.5 + index),
        }
        for index in range(8)
    )


def _quote(pair: CryptoPair):
    from app.crypto_research import CryptoObservation

    return CryptoObservation(
        pair, T0, Decimal("100"), Decimal("99.9"), Decimal("100.1"),
        None, high=Decimal("101"), low=Decimal("99"),
    )


def test_bounded_history_owner_uses_single_pair_m1_m5_and_publishes_contexts() -> None:
    pairs = tuple(CryptoPair.configured(value) for value in ("SOL/USD",))
    calls: list[tuple[str, str, int]] = []
    contexts = []

    class Store:
        def append(self, _record):
            return None

        def close(self):
            return None

    class Provider:
        def discover(self, _configured):
            return pairs

        def snapshots(self, selected):
            return tuple(_quote(pair) for pair in selected)

        def historical_bars(self, pair, *, timespan, count, real_time_required=False):
            assert len((pair,)) == 1
            calls.append((pair.canonical_symbol, timespan, count))
            return _rows(1 if timespan == "M1" else 5)

    runtime = CryptoResearchRuntime(
        enabled=True,
        provider=Provider(),
        configured_pairs=pairs,
        clock=lambda: T0,
        intelligence_context_sink=contexts.append,
        intelligence_history_max_symbols=3,
        intelligence_history_request_budget=6,
        store=Store(),
    )
    runtime._accepting = True
    runtime.refresh_once()
    runtime.close()

    assert len(calls) == 6
    assert {item[0] for item in calls} == {"SOL/USD", "BTC/USD", "ETH/USD"}
    assert {item[1] for item in calls} == {"M1", "M5"}
    assert all(item[2] == 64 for item in calls)
    assert contexts
    assert all(item.evidence.breadth.observed_universe_size == 3 for item in contexts)
    metrics = runtime.metrics()
    assert metrics.history_requests_attempted == 6
    assert metrics.history_retained_bars <= 3 * 2 * 8


def test_disabled_intelligence_path_makes_no_history_requests() -> None:
    class Store:
        def append(self, _record):
            return None

        def close(self):
            return None

    class Provider:
        def discover(self, _configured):
            return (CryptoPair.configured("BTC/USD"),)

        def snapshots(self, selected):
            return ()

        def historical_bars(self, *args, **kwargs):
            raise AssertionError("history must not be requested")

    runtime = CryptoResearchRuntime(
        enabled=True,
        provider=Provider(),
        configured_pairs=(CryptoPair.configured("BTC/USD"),),
        intelligence_context_sink=None,
        clock=lambda: T0,
        store=Store(),
    )
    runtime._accepting = True
    runtime.refresh_once()
    assert runtime.metrics().history_requests_attempted == 0
    runtime.close()

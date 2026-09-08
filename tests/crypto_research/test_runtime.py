from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from time import perf_counter, sleep

from app.crypto_research import (
    CryptoObservation,
    CryptoPair,
    CryptoResearchRuntime,
    CryptoResearchStatus,
    CryptoResearchViewStore,
    UnsupportedCryptoSymbolError,
)


T0 = datetime(2026, 9, 7, 12, tzinfo=UTC)


class MemoryStore:
    def __init__(self, fail=False):
        self.rows, self.fail, self.closed = [], fail, False

    def append(self, record):
        if self.fail:
            raise OSError("synthetic persistence failure")
        self.rows.append(dict(record))

    def close(self):
        self.closed = True


class View:
    def __init__(self):
        self.rows = ()

    def publish(self, rows):
        self.rows = rows


def observation(symbol="BTC", price="100", volume="10", at=T0):
    value = Decimal(price)
    return CryptoObservation(
        CryptoPair(symbol, "USD", symbol + "USD"), at, value,
        value - Decimal("0.1"), value + Decimal("0.1"),
        None if volume is None else Decimal(volume),
        high=value + Decimal("0.2"), low=value - Decimal("0.2"),
    )


def test_disabled_runtime_has_no_threads_output_or_provider_calls(tmp_path) -> None:
    runtime = CryptoResearchRuntime(enabled=False, path=tmp_path / "crypto.jsonl")
    assert runtime.start() is False
    assert runtime.admit(observation()) is False
    assert runtime.metrics().crypto_symbol_count == 0
    assert runtime._worker is None and runtime._poller is None
    assert not (tmp_path / "crypto.jsonl").exists()


def test_ten_thousand_identical_updates_persist_one_bounded_episode() -> None:
    store, view = MemoryStore(), View()
    runtime = CryptoResearchRuntime(
        enabled=True, store=store, view_store=view, queue_capacity=8,
        clock=lambda: T0,
    )
    runtime.start()
    started = perf_counter()
    latencies = []
    for _ in range(10_000):
        before = perf_counter()
        runtime.admit(observation())
        latencies.append(perf_counter() - before)
    elapsed = perf_counter() - started
    assert runtime.close(timeout_seconds=2)
    metrics = runtime.metrics()
    assert len(store.rows) == metrics.crypto_episodes_persisted == 1
    assert metrics.crypto_duplicates_suppressed == 9_999
    assert metrics.crypto_queue_high_water <= 1
    assert metrics.crypto_retained_state_count == 1
    assert elapsed / 10_000 < 0.01
    assert max(latencies) < 0.1
    assert view.rows and view.rows[0].execution_authorized is False


def test_stale_queue_full_persistence_and_shutdown_failures_are_isolated() -> None:
    stale = CryptoResearchRuntime(enabled=True, store=MemoryStore(), clock=lambda: T0)
    stale.start()
    assert not stale.admit(observation(at=T0 - timedelta(minutes=3)))
    assert stale.metrics().stale_quotes == 1
    assert stale.close()

    queued = CryptoResearchRuntime(
        enabled=True, store=MemoryStore(), queue_capacity=1, clock=lambda: T0,
    )
    queued._accepting = True
    assert queued.admit(observation("BTC"))
    assert not queued.admit(observation("ETH"))
    assert queued.metrics().queue_rejections == 1
    assert queued.close()

    failing_store = MemoryStore(fail=True)
    failing = CryptoResearchRuntime(
        enabled=True, store=failing_store, clock=lambda: T0,
    )
    failing.start()
    assert failing.admit(observation())
    assert failing.close(timeout_seconds=2)
    assert failing.metrics().persistence_failures == 1
    assert failing.metrics().stopped


def test_retained_symbols_history_signatures_and_memory_metrics_are_bounded() -> None:
    store = MemoryStore()
    runtime = CryptoResearchRuntime(
        enabled=True, store=store, retained_symbol_limit=2,
        history_capacity=2, signature_capacity=2, queue_capacity=8,
        clock=lambda: T0,
    )
    runtime.start()
    for symbol in ("BTC", "ETH", "SOL"):
        assert runtime.admit(observation(symbol))
    assert runtime.admit(observation("SOL", price="101"))
    assert runtime.close(timeout_seconds=2)
    metrics = runtime.memory_metrics()
    assert metrics["crypto_symbol_count"] == 2
    assert metrics["crypto_retained_state_count"] == 2
    assert metrics["crypto_history_total_observation_count"] == 3
    assert metrics["crypto_history_max_observations_per_symbol"] == 2
    assert metrics["crypto_history_per_symbol_maximum"] == 2
    assert metrics["crypto_history_admissions"] == 4
    assert metrics["crypto_history_symbol_evictions"] == 1
    assert metrics["crypto_history_observation_evictions"] == 1
    assert len(runtime._admission_signatures) == 2
    assert all(len(values) <= 2 for values in runtime._history.values())
    assert set(metrics) == {
        "crypto_symbol_count", "crypto_retained_state_count",
        "crypto_queue_depth", "crypto_queue_high_water",
        "crypto_episodes_persisted", "crypto_duplicates_suppressed",
        "crypto_provider_failures", "crypto_malformed_quotes",
        "crypto_unsupported_symbols", "crypto_snapshot_batches_requested",
        "crypto_snapshot_batches_succeeded", "crypto_snapshot_batches_failed",
        "crypto_snapshot_symbols_requested", "crypto_snapshot_symbols_returned",
        "crypto_refreshes_partial", "crypto_refreshes_complete",
        "crypto_history_total_observation_count",
        "crypto_history_max_observations_per_symbol",
        "crypto_history_per_symbol_maximum", "crypto_history_admissions",
        "crypto_history_observation_evictions",
        "crypto_history_symbol_evictions",
    }


def test_failed_snapshot_batch_does_not_prevent_later_batches() -> None:
    pairs = tuple(CryptoPair(f"S{index:02}", "USD", f"S{index:02}USD") for index in range(45))

    class Provider:
        def __init__(self):
            self.calls = []

        def discover(self, _configured):
            return pairs

        def snapshots(self, batch):
            self.calls.append(tuple(pair.canonical_symbol for pair in batch))
            if batch[0].base_asset == "S20":
                raise RuntimeError("sensitive provider failure")
            return tuple(observation(pair.base_asset) for pair in batch)

    provider = Provider()
    view = CryptoResearchViewStore()
    runtime = CryptoResearchRuntime(
        enabled=True, provider=provider, store=MemoryStore(),
        view_store=view, queue_capacity=64, clock=lambda: T0,
    )
    runtime._accepting = True

    assert runtime.refresh_once() == 25
    assert len(provider.calls) == 3
    assert provider.calls[-1] == tuple(f"S{index:02}/USD" for index in range(40, 45))
    metrics = runtime.metrics()
    assert metrics.provider_failures == 1
    assert metrics.snapshot_batches_requested == 3
    assert metrics.snapshot_batches_succeeded == 2
    assert metrics.snapshot_batches_failed == 1
    assert metrics.snapshot_symbols_requested == 45
    assert metrics.snapshot_symbols_returned == 25
    assert metrics.refreshes_partial == 1
    assert metrics.refreshes_complete == 0
    state = view.status_snapshot()
    assert state.status is CryptoResearchStatus.PARTIAL_DATA
    assert state.last_failure_category == "PROVIDER_ERROR"
    runtime.close()


def test_all_success_and_total_snapshot_failure_statuses_are_honest() -> None:
    pairs = tuple(CryptoPair(symbol, "USD", symbol + "USD") for symbol in ("BTC", "ETH"))

    class Provider:
        fail = False

        def discover(self, _configured):
            return pairs

        def snapshots(self, batch):
            if self.fail:
                raise RuntimeError("provider detail")
            return tuple(observation(pair.base_asset) for pair in batch)

    provider = Provider()
    view = CryptoResearchViewStore()
    runtime = CryptoResearchRuntime(
        enabled=True, provider=provider, store=MemoryStore(),
        view_store=view, clock=lambda: T0,
    )
    runtime._accepting = True
    assert runtime.refresh_once() == 2
    assert view.status_snapshot().status is CryptoResearchStatus.ACTIVE
    assert runtime.metrics().refreshes_complete == 1

    provider.fail = True
    assert runtime.refresh_once() == 0
    assert view.status_snapshot().status is CryptoResearchStatus.PROVIDER_ERROR
    assert runtime.metrics().provider_failures == 1
    assert runtime.metrics().snapshot_batches_failed == 1
    runtime.close()


def test_provider_failure_is_contained_without_touching_admission_worker() -> None:
    class Provider:
        def discover(self, _configured):
            raise RuntimeError("provider down")

    runtime = CryptoResearchRuntime(
        enabled=True, provider=Provider(), store=MemoryStore(), clock=lambda: T0,
    )
    runtime.start()
    deadline = perf_counter() + 1
    while runtime.metrics().provider_failures == 0 and perf_counter() < deadline:
        sleep(0.01)
    assert runtime.metrics().provider_failures >= 1
    assert runtime.memory_metrics()["crypto_provider_failures"] >= 1
    assert runtime.admit(observation())
    assert runtime.close(timeout_seconds=2)


def test_provider_status_and_sanitized_failure_categories_are_observable() -> None:
    class EmptyProvider:
        def discover(self, _configured):
            return ()

    view = CryptoResearchViewStore()
    runtime = CryptoResearchRuntime(
        enabled=True, provider=EmptyProvider(), store=MemoryStore(),
        view_store=view, clock=lambda: T0,
    )
    assert view.status_snapshot().status is CryptoResearchStatus.DISCOVERING
    assert runtime.refresh_once() == 0
    assert view.status_snapshot().status is CryptoResearchStatus.NO_SUPPORTED_PAIRS

    class UnsupportedProvider:
        def discover(self, _configured):
            raise UnsupportedCryptoSymbolError("sensitive provider detail")

    runtime._provider = UnsupportedProvider()
    assert runtime.refresh_once() == 0
    state = view.status_snapshot()
    assert state.status is CryptoResearchStatus.PROVIDER_ERROR
    assert state.last_failure_category == "UNSUPPORTED_SYMBOL"
    metrics = runtime.memory_metrics()
    assert metrics["crypto_provider_failures"] == 1
    assert metrics["crypto_unsupported_symbols"] == 1
    assert "sensitive" not in state.last_failure_category
    assert runtime.close()


def test_append_only_jsonl_contains_required_authority_and_point_in_time_fields(
    tmp_path,
) -> None:
    path = tmp_path / "crypto-research.jsonl"
    runtime = CryptoResearchRuntime(enabled=True, path=path, clock=lambda: T0)
    runtime.start()
    assert runtime.admit(observation())
    assert runtime.close(timeout_seconds=2)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert set(rows[0]) >= {
        "schema_version", "asset_type", "canonical_symbol", "provider_symbol",
        "base_asset", "quote_asset", "timestamp", "decision_cutoff", "price",
        "bid", "ask", "spread", "volume", "features", "event_types", "score",
        "rank", "research_only", "production_promoted", "selection_authorized",
        "execution_authorized",
    }
    assert rows[0]["research_only"] is True
    assert rows[0]["execution_authorized"] is False


def test_append_only_jsonl_serializes_unavailable_volume_as_null(tmp_path) -> None:
    path = tmp_path / "crypto-no-volume.jsonl"
    runtime = CryptoResearchRuntime(enabled=True, path=path, clock=lambda: T0)
    runtime.start()
    assert runtime.admit(observation(volume=None))
    assert runtime.close(timeout_seconds=2)
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["volume"] is None
    assert row["features"]["volume"] is None
    assert row["features"]["notional_volume"] is None

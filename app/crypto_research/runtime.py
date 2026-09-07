"""Bounded, failure-isolated crypto research runtime with zero authority."""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread

from .analysis import calculate_features, detect_events, score_features
from .models import (
    CryptoObservation,
    CryptoPair,
    CryptoResearchDecision,
    crypto_regime,
)
from .persistence import CryptoJsonLinesStore, CryptoResearchStore
from .provider import WebullCryptoResearchProvider
from .view import CryptoResearchViewStore, default_crypto_research_view


@dataclass(frozen=True, slots=True)
class CryptoResearchMetrics:
    enabled: bool
    crypto_symbol_count: int
    crypto_retained_state_count: int
    crypto_queue_depth: int
    crypto_queue_high_water: int
    crypto_episodes_persisted: int
    crypto_duplicates_suppressed: int
    provider_failures: int
    malformed_quotes: int
    stale_quotes: int
    queue_rejections: int
    persistence_failures: int
    accepting: bool
    stopped: bool


class CryptoResearchRuntime:
    """Owns crypto state only; exposes no selection or execution interface."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        provider: WebullCryptoResearchProvider | None = None,
        configured_pairs: Sequence[CryptoPair] = (),
        path="crypto-research.jsonl",
        queue_capacity: int = 256,
        refresh_seconds: float = 60.0,
        retained_symbol_limit: int = 256,
        history_capacity: int = 240,
        signature_capacity: int = 4096,
        maximum_quote_age_seconds: float = 120.0,
        store: CryptoResearchStore | None = None,
        view_store: CryptoResearchViewStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if min(
            queue_capacity,
            retained_symbol_limit,
            history_capacity,
            signature_capacity,
        ) <= 0:
            raise ValueError("crypto research bounds must be positive")
        if refresh_seconds <= 0 or maximum_quote_age_seconds <= 0:
            raise ValueError("crypto research timing bounds must be positive")
        self.enabled = bool(enabled)
        self._provider = provider
        self._configured_pairs = tuple(configured_pairs)
        self._store = store or CryptoJsonLinesStore(path)
        self._view = view_store or default_crypto_research_view()
        self._queue: Queue[CryptoObservation] = Queue(maxsize=queue_capacity)
        self._refresh_seconds = refresh_seconds
        self._retained_symbol_limit = retained_symbol_limit
        self._history_capacity = history_capacity
        self._signature_capacity = signature_capacity
        self._maximum_quote_age_seconds = maximum_quote_age_seconds
        self._clock = clock
        self._lock = RLock()
        self._stop = Event()
        self._worker: Thread | None = None
        self._poller: Thread | None = None
        self._pairs: tuple[CryptoPair, ...] = ()
        self._history: OrderedDict[str, deque[CryptoObservation]] = OrderedDict()
        self._latest: OrderedDict[str, CryptoResearchDecision] = OrderedDict()
        self._admission_signatures: OrderedDict[tuple[object, ...], None] = OrderedDict()
        self._queue_high_water = 0
        self._episodes_persisted = 0
        self._duplicates_suppressed = 0
        self._provider_failures = 0
        self._malformed_quotes = 0
        self._stale_quotes = 0
        self._queue_rejections = 0
        self._persistence_failures = 0
        self._accepting = False
        self._stopped = not self.enabled
        if not self.enabled:
            self._view.publish(())

    def start(self) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if self._worker is not None:
                return False
            self._accepting = True
            self._stopped = False
            self._worker = Thread(
                target=self._run_worker, name="atlas-crypto-research-writer", daemon=True
            )
            self._worker.start()
            if self._provider is not None:
                self._poller = Thread(
                    target=self._run_poller,
                    name="atlas-crypto-research-provider",
                    daemon=True,
                )
                self._poller.start()
            return True

    def refresh_once(self) -> int:
        """Perform one isolated provider refresh; useful for controlled diagnostics."""
        if not self.enabled or self._provider is None:
            return 0
        try:
            pairs = self._provider.discover(self._configured_pairs)
            with self._lock:
                self._pairs = pairs[: self._retained_symbol_limit]
            accepted = 0
            for offset in range(0, len(self._pairs), 20):
                observations = self._provider.snapshots(
                    self._pairs[offset : offset + 20]
                )
                accepted += sum(1 for item in observations if self.admit(item))
            return accepted
        except Exception:
            with self._lock:
                self._provider_failures += 1
            return 0

    def admit(self, observation: CryptoObservation) -> bool:
        """Nonblocking producer admission with bounded semantic deduplication."""
        if not self.enabled:
            return False
        now = self._clock()
        if (now - observation.timestamp).total_seconds() > self._maximum_quote_age_seconds:
            with self._lock:
                self._stale_quotes += 1
            return False
        signature = _observation_signature(observation)
        with self._lock:
            if not self._accepting:
                self._queue_rejections += 1
                return False
            if signature in self._admission_signatures:
                self._duplicates_suppressed += 1
                return False
            try:
                self._queue.put_nowait(observation)
            except Full:
                self._queue_rejections += 1
                return False
            if all(
                item.canonical_symbol != observation.pair.canonical_symbol
                for item in self._pairs
            ):
                self._pairs = (*self._pairs, observation.pair)[
                    -self._retained_symbol_limit :
                ]
            self._admission_signatures[signature] = None
            self._admission_signatures.move_to_end(signature)
            while len(self._admission_signatures) > self._signature_capacity:
                self._admission_signatures.popitem(last=False)
            self._queue_high_water = max(self._queue_high_water, self._queue.qsize())
            return True

    def latest(self) -> tuple[CryptoResearchDecision, ...]:
        with self._lock:
            return tuple(
                sorted(self._latest.values(), key=lambda item: (item.rank, item.pair.canonical_symbol))
            )

    def metrics(self) -> CryptoResearchMetrics:
        with self._lock:
            return CryptoResearchMetrics(
                self.enabled,
                len(self._pairs),
                len(self._history),
                self._queue.qsize(),
                self._queue_high_water,
                self._episodes_persisted,
                self._duplicates_suppressed,
                self._provider_failures,
                self._malformed_quotes,
                self._stale_quotes,
                self._queue_rejections,
                self._persistence_failures,
                self._accepting,
                self._stopped,
            )

    def memory_metrics(self) -> dict[str, int]:
        metrics = self.metrics()
        return {
            "crypto_symbol_count": metrics.crypto_symbol_count,
            "crypto_retained_state_count": metrics.crypto_retained_state_count,
            "crypto_queue_depth": metrics.crypto_queue_depth,
            "crypto_queue_high_water": metrics.crypto_queue_high_water,
            "crypto_episodes_persisted": metrics.crypto_episodes_persisted,
            "crypto_duplicates_suppressed": metrics.crypto_duplicates_suppressed,
        }

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        with self._lock:
            self._accepting = False
            self._stop.set()
        poller = self._poller
        if poller is not None:
            poller.join(timeout_seconds)
        worker = self._worker
        if worker is not None:
            worker.join(timeout_seconds)
        stopped = (poller is None or not poller.is_alive()) and (
            worker is None or not worker.is_alive()
        )
        try:
            self._store.close()
        except Exception:
            with self._lock:
                self._persistence_failures += 1
        with self._lock:
            self._stopped = stopped
        return stopped

    def _run_poller(self) -> None:
        while not self._stop.is_set():
            self.refresh_once()
            if self._stop.wait(self._refresh_seconds):
                return

    def _run_worker(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                observation = self._queue.get(timeout=0.05)
            except Empty:
                continue
            try:
                self._evaluate_and_persist(observation)
            except Exception:
                with self._lock:
                    self._malformed_quotes += 1
            finally:
                self._queue.task_done()

    def _evaluate_and_persist(self, observation: CryptoObservation) -> None:
        key = observation.pair.canonical_symbol
        cutoff = self._clock()
        if observation.timestamp > cutoff:
            raise ValueError("crypto observation exceeds decision cutoff")
        with self._lock:
            history = self._history.get(key)
            if history is None:
                history = deque(maxlen=self._history_capacity)
                self._history[key] = history
            history.append(observation)
            self._history.move_to_end(key)
            while len(self._history) > self._retained_symbol_limit:
                removed, _ = self._history.popitem(last=False)
                self._latest.pop(removed, None)
            evidence = tuple(history)
        features = calculate_features(evidence, cutoff=cutoff)
        events = detect_events(evidence, features)
        score, components = score_features(features, events)
        decision = CryptoResearchDecision(
            schema_version="1",
            pair=observation.pair,
            timestamp=observation.timestamp,
            decision_cutoff=cutoff,
            price=observation.price,
            bid=observation.bid,
            ask=observation.ask,
            spread=features.spread,
            volume=observation.volume,
            features=features,
            event_types=events,
            score=score,
            score_components=components,
            rank=1,
            regime=crypto_regime(cutoff),
        )
        with self._lock:
            prospective = dict(self._latest)
            prospective[key] = decision
            ordered = sorted(
                prospective.values(),
                key=lambda item: (-item.score, item.pair.canonical_symbol),
            )
            ranked = tuple(replace(item, rank=index + 1) for index, item in enumerate(ordered))
            decision = next(item for item in ranked if item.pair.canonical_symbol == key)
            self._latest = OrderedDict(
                (item.pair.canonical_symbol, item) for item in ranked
            )
        try:
            self._store.append(decision.to_record())
        except Exception:
            with self._lock:
                self._persistence_failures += 1
        else:
            with self._lock:
                self._episodes_persisted += 1
        self._view.publish(self.latest())


def _observation_signature(observation: CryptoObservation) -> tuple[object, ...]:
    return (
        observation.asset_type,
        observation.pair.canonical_symbol,
        observation.pair.provider_symbol,
        observation.price,
        observation.bid,
        observation.ask,
        observation.volume,
        observation.high,
        observation.low,
    )


__all__ = ["CryptoResearchMetrics", "CryptoResearchRuntime"]

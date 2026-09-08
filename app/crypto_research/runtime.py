"""Bounded, failure-isolated crypto research runtime with zero authority."""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread

from .analysis import calculate_features, detect_events, score_features
from .evidence import (
    BoundedCryptoBarHistory,
    CryptoBarInterval,
    CryptoCompletedBar,
    build_crypto_evidence_context,
    normalize_crypto_rows,
)
from .models import (
    CryptoObservation,
    CryptoPair,
    CryptoResearchDecision,
    crypto_regime,
)
from .outcomes import CryptoOutcomeDecision
from .strategies import CryptoDiscoveryContext
from .persistence import CryptoJsonLinesStore, CryptoResearchStore
from .provider import (
    MalformedCryptoQuoteError,
    UnsupportedCryptoSymbolError,
    WebullCryptoResearchProvider,
)
from .view import (
    CryptoResearchStatus,
    CryptoResearchViewStore,
    default_crypto_research_view,
)


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
    snapshot_batches_requested: int
    snapshot_batches_succeeded: int
    snapshot_batches_failed: int
    snapshot_symbols_requested: int
    snapshot_symbols_returned: int
    refreshes_partial: int
    refreshes_complete: int
    malformed_quotes: int
    unsupported_symbols: int
    stale_quotes: int
    queue_rejections: int
    persistence_failures: int
    accepting: bool
    stopped: bool
    history_requests_attempted: int = 0
    history_requests_succeeded: int = 0
    history_requests_failed: int = 0
    history_rows_seen: int = 0
    history_bars_admitted: int = 0
    history_bars_duplicate: int = 0
    history_bars_rejected: int = 0
    history_series_count: int = 0
    history_retained_bars: int = 0
    shortlist_size: int = 0
    shortlist_high_water: int = 0
    shortlist_admissions: int = 0
    shortlist_removals: int = 0
    shortlist_deferred: int = 0
    m1_requests: int = 0
    m5_requests: int = 0
    phase_b_contexts_built: int = 0
    phase_b_contexts_unavailable: int = 0
    intelligence_contexts_published: int = 0
    intelligence_submit_failures: int = 0
    outcome_update_cycles: int = 0


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
        intelligence_context_sink: Callable[[CryptoDiscoveryContext], object] | None = None,
        intelligence_outcome_sink: Callable[..., object] | None = None,
        intelligence_history_max_symbols: int = 10,
        intelligence_history_bar_count: int = 64,
        intelligence_history_request_budget: int = 20,
        intelligence_m1_refresh_seconds: float = 60.0,
        intelligence_m5_refresh_seconds: float = 60.0,
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
        if not 3 <= intelligence_history_max_symbols <= 256:
            raise ValueError("intelligence history symbol bound must be 3..256")
        if not 1 <= intelligence_history_bar_count <= 64:
            raise ValueError("intelligence history bar count must be 1..64")
        if intelligence_history_request_budget <= 0 or intelligence_m1_refresh_seconds <= 0 or intelligence_m5_refresh_seconds <= 0:
            raise ValueError("intelligence history timing/budget bounds must be positive")
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
        self._intelligence_context_sink = intelligence_context_sink
        self._intelligence_outcome_sink = intelligence_outcome_sink
        self._bar_history = BoundedCryptoBarHistory(
            maximum_symbols=intelligence_history_max_symbols,
            bars_per_series=intelligence_history_bar_count,
        )
        self._history_max_symbols = intelligence_history_max_symbols
        self._history_bar_count = intelligence_history_bar_count
        self._history_request_budget = intelligence_history_request_budget
        self._m1_refresh_seconds = intelligence_m1_refresh_seconds
        self._m5_refresh_seconds = intelligence_m5_refresh_seconds
        self._history_cycle = 0
        self._history_last_requested: dict[tuple[str, CryptoBarInterval], datetime] = {}
        self._shortlist: tuple[CryptoPair, ...] = ()
        self._intelligence_decisions: dict[str, CryptoOutcomeDecision] = {}
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
        self._snapshot_batches_requested = 0
        self._snapshot_batches_succeeded = 0
        self._snapshot_batches_failed = 0
        self._snapshot_symbols_requested = 0
        self._snapshot_symbols_returned = 0
        self._refreshes_partial = 0
        self._refreshes_complete = 0
        self._malformed_quotes = 0
        self._unsupported_symbols = 0
        self._stale_quotes = 0
        self._queue_rejections = 0
        self._persistence_failures = 0
        self._history_requests_attempted = 0
        self._history_requests_succeeded = 0
        self._history_requests_failed = 0
        self._history_rows_seen = 0
        self._history_bars_admitted = 0
        self._history_bars_duplicate = 0
        self._history_bars_rejected = 0
        self._shortlist_high_water = 0
        self._shortlist_admissions = 0
        self._shortlist_removals = 0
        self._shortlist_deferred = 0
        self._m1_requests = 0
        self._m5_requests = 0
        self._phase_b_contexts_built = 0
        self._phase_b_contexts_unavailable = 0
        self._intelligence_contexts_published = 0
        self._intelligence_submit_failures = 0
        self._outcome_update_cycles = 0
        self._accepting = False
        self._stopped = not self.enabled
        if not self.enabled:
            self._view.publish(())
            self._publish_status(CryptoResearchStatus.DISABLED)
        else:
            self._publish_status(CryptoResearchStatus.DISCOVERING)

    def start(self) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if self._worker is not None:
                return False
            self._accepting = True
            self._stopped = False
            self._publish_status(CryptoResearchStatus.DISCOVERING)
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

    def configure_intelligence(
        self,
        *,
        context_sink: Callable[[CryptoDiscoveryContext], object] | None,
        outcome_sink: Callable[..., object] | None,
    ) -> None:
        """Attach the downstream research bridge without importing its internals."""
        with self._lock:
            self._intelligence_context_sink = context_sink
            self._intelligence_outcome_sink = outcome_sink

    def _research_shortlist(self) -> tuple[CryptoPair, ...]:
        with self._lock:
            available = {pair.canonical_symbol: pair for pair in self._pairs}
            ranked = sorted(
                self._latest.values(),
                key=lambda item: (-item.score, item.pair.canonical_symbol),
            )
            ordered: list[CryptoPair] = []
            for pair in self._configured_pairs:
                available.setdefault(pair.canonical_symbol, pair)
            for symbol in ("BTC/USD", "ETH/USD"):
                pair = available.get(symbol) or CryptoPair.configured(symbol)
                available[symbol] = pair
                if pair not in ordered:
                    ordered.append(pair)
            for decision in ranked:
                pair = available.get(decision.pair.canonical_symbol)
                if pair is not None and pair not in ordered:
                    ordered.append(pair)
            for pair in sorted(available.values(), key=lambda item: item.canonical_symbol):
                if pair not in ordered:
                    ordered.append(pair)
            selected = tuple(ordered[: self._history_max_symbols])
            previous = {item.canonical_symbol for item in self._shortlist}
            current = {item.canonical_symbol for item in selected}
            self._shortlist = selected
            self._shortlist_admissions += len(current - previous)
            self._shortlist_removals += len(previous - current)
            self._shortlist_high_water = max(self._shortlist_high_water, len(selected))
            self._history_last_requested = {
                key: value
                for key, value in self._history_last_requested.items()
                if key[0] in current
            }
            self._shortlist_deferred += max(0, len(available) - len(selected))
            return selected

    def _refresh_completed_bars(self) -> None:
        sink = self._intelligence_context_sink
        if sink is None or self._provider is None or not self.enabled:
            return
        shortlist = self._research_shortlist()
        cutoff = self._clock()
        due: list[tuple[CryptoPair, CryptoBarInterval]] = []
        for pair in shortlist:
            for interval, cadence in (
                (CryptoBarInterval.M1, self._m1_refresh_seconds),
                (CryptoBarInterval.M5, self._m5_refresh_seconds),
            ):
                key = (pair.canonical_symbol, interval)
                last = self._history_last_requested.get(key)
                if last is None or (cutoff - last).total_seconds() >= cadence:
                    due.append((pair, interval))
        due = due[: self._history_request_budget]
        for pair, interval in due:
            self._history_last_requested[(pair.canonical_symbol, interval)] = cutoff
            with self._lock:
                self._history_requests_attempted += 1
                if interval is CryptoBarInterval.M1:
                    self._m1_requests += 1
                else:
                    self._m5_requests += 1
            try:
                rows = self._provider.historical_bars(
                    pair,
                    timespan=interval.value,
                    count=self._history_bar_count,
                    real_time_required=False,
                )
                observed_at = self._clock()
                result = normalize_crypto_rows(
                    pair,
                    rows,
                    interval=interval,
                    observed_at=observed_at,
                    decision_cutoff=observed_at,
                )
                before = self._bar_history.metrics().crypto_evidence_duplicates_suppressed
                admitted = self._bar_history.add_many(result.bars)
                after = self._bar_history.metrics().crypto_evidence_duplicates_suppressed
                with self._lock:
                    self._history_requests_succeeded += 1
                    self._history_rows_seen += len(rows)
                    self._history_bars_admitted += admitted
                    self._history_bars_duplicate += max(0, after - before) + result.duplicate_rows
                    self._history_bars_rejected += result.malformed_rows + result.incomplete_rows
            except Exception:
                with self._lock:
                    self._history_requests_failed += 1
                continue
        cutoff = self._clock()
        self._update_intelligence_outcomes()
        self._publish_discovery_contexts(shortlist, cutoff)

    def _update_intelligence_outcomes(self) -> None:
        sink = self._intelligence_outcome_sink
        if sink is None:
            return
        for decision_id, decision in tuple(self._intelligence_decisions.items()):
            bars = self._bar_history.bars(decision.canonical_symbol, CryptoBarInterval.M1)
            if not bars:
                bars = self._bar_history.bars(decision.canonical_symbol, CryptoBarInterval.M5)
            try:
                result = sink(
                    decision_id,
                    bars,
                    btc_bars=self._bar_history.bars("BTC/USD", CryptoBarInterval.M1),
                    eth_bars=self._bar_history.bars("ETH/USD", CryptoBarInterval.M1),
                )
                if isinstance(result, Iterable):
                    horizons = {getattr(item, "horizon_seconds", None) for item in result}
                    if {60, 300, 900, 1800}.issubset(horizons):
                        self._intelligence_decisions.pop(decision_id, None)
                self._outcome_update_cycles += 1
            except Exception:
                continue

    def _publish_discovery_contexts(self, shortlist: Sequence[CryptoPair], cutoff: datetime) -> None:
        sink = self._intelligence_context_sink
        if sink is None:
            return
        for pair in shortlist:
            if pair.canonical_symbol in {"BTC/USD", "ETH/USD"}:
                continue
            try:
                if not self._bar_history.bars(pair.canonical_symbol, CryptoBarInterval.M5):
                    with self._lock:
                        self._phase_b_contexts_unavailable += 1
                    continue
                context = CryptoDiscoveryContext(
                    build_crypto_evidence_context(
                        pair,
                        self._bar_history,
                        decision_cutoff=cutoff,
                        interval=CryptoBarInterval.M5,
                        observed_universe_size=len(shortlist),
                    )
                )
                result = sink(context)
                with self._lock:
                    self._phase_b_contexts_built += 1
                    self._intelligence_contexts_published += 1
                if isinstance(result, Iterable):
                    for decision in result:
                        if isinstance(decision, CryptoOutcomeDecision):
                            self._intelligence_decisions[decision.identity.deterministic_id] = decision
                            while len(self._intelligence_decisions) > 4096:
                                self._intelligence_decisions.pop(next(iter(self._intelligence_decisions)))
            except Exception:
                with self._lock:
                    self._phase_b_contexts_unavailable += 1
                    self._intelligence_submit_failures += 1

    def refresh_once(self) -> int:
        """Perform one isolated provider refresh; useful for controlled diagnostics."""
        if not self.enabled or self._provider is None:
            return 0
        self._publish_status(CryptoResearchStatus.DISCOVERING)
        try:
            pairs = self._provider.discover(self._configured_pairs)
            with self._lock:
                self._pairs = pairs[: self._retained_symbol_limit]
            if not self._pairs:
                self._publish_status(CryptoResearchStatus.NO_SUPPORTED_PAIRS)
                return 0
            accepted = 0
            received = 0
            succeeded = 0
            failed = 0
            last_failure_category: str | None = None
            for offset in range(0, len(self._pairs), 20):
                batch = self._pairs[offset : offset + 20]
                with self._lock:
                    self._snapshot_batches_requested += 1
                    self._snapshot_symbols_requested += len(batch)
                try:
                    observations = self._provider.snapshots(batch)
                except Exception as exc:
                    category = _failure_category(exc)
                    with self._lock:
                        self._provider_failures += 1
                        self._snapshot_batches_failed += 1
                        if isinstance(exc, MalformedCryptoQuoteError):
                            self._malformed_quotes += 1
                        if isinstance(exc, UnsupportedCryptoSymbolError):
                            self._unsupported_symbols += 1
                    failed += 1
                    last_failure_category = category
                    continue
                with self._lock:
                    self._snapshot_batches_succeeded += 1
                    self._snapshot_symbols_returned += len(observations)
                succeeded += 1
                received += len(observations)
                accepted += sum(1 for item in observations if self.admit(item))
            if failed and received:
                with self._lock:
                    self._refreshes_partial += 1
                self._publish_status(
                    CryptoResearchStatus.PARTIAL_DATA,
                    last_failure_category,
                )
            elif failed:
                self._publish_status(
                    CryptoResearchStatus.PROVIDER_ERROR,
                    last_failure_category,
                )
            elif succeeded:
                with self._lock:
                    self._refreshes_complete += 1
                self._publish_status(
                    CryptoResearchStatus.ACTIVE
                    if received
                    else CryptoResearchStatus.AWAITING_DATA
                )
            self._refresh_completed_bars()
            return accepted
        except Exception as exc:
            with self._lock:
                self._provider_failures += 1
                if isinstance(exc, MalformedCryptoQuoteError):
                    self._malformed_quotes += 1
                if isinstance(exc, UnsupportedCryptoSymbolError):
                    self._unsupported_symbols += 1
            self._publish_status(
                CryptoResearchStatus.PROVIDER_ERROR,
                _failure_category(exc),
            )
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
                self._snapshot_batches_requested,
                self._snapshot_batches_succeeded,
                self._snapshot_batches_failed,
                self._snapshot_symbols_requested,
                self._snapshot_symbols_returned,
                self._refreshes_partial,
                self._refreshes_complete,
                self._malformed_quotes,
                self._unsupported_symbols,
                self._stale_quotes,
                self._queue_rejections,
                self._persistence_failures,
                self._accepting,
                self._stopped,
                self._history_requests_attempted,
                self._history_requests_succeeded,
                self._history_requests_failed,
                self._history_rows_seen,
                self._history_bars_admitted,
                self._history_bars_duplicate,
                self._history_bars_rejected,
                self._bar_history.metrics().crypto_bar_series,
                self._bar_history.metrics().crypto_completed_bars_retained,
                len(self._shortlist),
                self._shortlist_high_water,
                self._shortlist_admissions,
                self._shortlist_removals,
                self._shortlist_deferred,
                self._m1_requests,
                self._m5_requests,
                self._phase_b_contexts_built,
                self._phase_b_contexts_unavailable,
                self._intelligence_contexts_published,
                self._intelligence_submit_failures,
                self._outcome_update_cycles,
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
            "crypto_provider_failures": metrics.provider_failures,
            "crypto_snapshot_batches_requested": metrics.snapshot_batches_requested,
            "crypto_snapshot_batches_succeeded": metrics.snapshot_batches_succeeded,
            "crypto_snapshot_batches_failed": metrics.snapshot_batches_failed,
            "crypto_snapshot_symbols_requested": metrics.snapshot_symbols_requested,
            "crypto_snapshot_symbols_returned": metrics.snapshot_symbols_returned,
            "crypto_refreshes_partial": metrics.refreshes_partial,
            "crypto_refreshes_complete": metrics.refreshes_complete,
            "crypto_malformed_quotes": metrics.malformed_quotes,
            "crypto_unsupported_symbols": metrics.unsupported_symbols,
        }

    def _publish_status(
        self,
        status: CryptoResearchStatus,
        last_failure_category: str | None = None,
    ) -> None:
        publisher = getattr(self._view, "publish_status", None)
        if callable(publisher):
            publisher(status, last_failure_category)

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


def _failure_category(exc: Exception) -> str:
    if isinstance(exc, UnsupportedCryptoSymbolError):
        return "UNSUPPORTED_SYMBOL"
    if isinstance(exc, MalformedCryptoQuoteError):
        return "MALFORMED_QUOTE"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, ValueError):
        return "NORMALIZATION_ERROR"
    return "PROVIDER_ERROR"


__all__ = ["CryptoResearchMetrics", "CryptoResearchRuntime"]

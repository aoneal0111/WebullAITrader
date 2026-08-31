"""Project immutable scanner snapshots onto the existing desktop watchlist."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
import logging

from app.live_scanner.session import scanner_session
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeWatchlistQuote,
    RuntimeWatchlistUpdate,
)
from app.realtime_scanner.models import ScannerSnapshot
from app.momentum_scanner.models import ScannerDecision
from app.performance_diagnostics import (
    PerformanceDiagnostics,
    performance_diagnostics,
)


_LOGGER = logging.getLogger("atlas.scanner")

_NEAR_MISS_RULES = frozenset({
    "price_range",
    "percentage_change",
    "relative_volume",
    "low_float",
    "dollar_volume",
    "spread",
})


def _scanner_classification(
    decision: ScannerDecision,
    ranked_symbols: set[str],
) -> str | None:
    if decision.symbol in ranked_symbols:
        return "QUALIFYING"
    if decision.technical_qualifies_without_catalyst:
        return "WATCHING"
    if (
        len(decision.technical_failed_rules) == 1
        and decision.technical_failed_rules[0] in _NEAR_MISS_RULES
    ):
        return "NEAR MISS"
    return None


class ScannerSnapshotPublisher:
    def __init__(
        self,
        event_sink: Callable[[PaperRuntimeEvent], None],
        sequence_source: Callable[[], int],
        *,
        source: str,
        stale_after: timedelta,
        diagnostics: PerformanceDiagnostics = performance_diagnostics,
    ) -> None:
        if not callable(event_sink) or not callable(sequence_source):
            raise TypeError("scanner publisher sinks must be callable")
        if stale_after <= timedelta():
            raise ValueError("scanner stale_after must be positive")
        self._sink = event_sink
        self._sequence = sequence_source
        self._source = source
        self._stale_after = stale_after
        self._published_symbols: set[str] = set()
        self._displayed_symbols: set[str] = set()
        self._published_decisions: dict[str, ScannerDecision] = {}
        self._last_decisions: dict[str, ScannerDecision] = {}
        self._last_fingerprint: object | None = None
        self._published_display_fingerprints: dict[str, object] = {}
        self._latest_snapshot: ScannerSnapshot | None = None
        self._last_changed = False
        self._last_stale_symbols: tuple[str, ...] = ()
        self._published_experiments: dict[str, tuple[object, ...]] = {}
        self._diagnostics = diagnostics

    @property
    def last_changed(self) -> bool:
        return self._last_changed

    @property
    def last_stale_symbols(self) -> tuple[str, ...]:
        return self._last_stale_symbols

    @property
    def authoritative_snapshot(self) -> ScannerSnapshot | None:
        """Return the latest full immutable scanner snapshot for recovery."""
        return self._latest_snapshot

    def publish(
        self,
        snapshot: ScannerSnapshot,
        *,
        cycle: int,
        now: datetime,
    ) -> tuple[str, ...]:
        self._latest_snapshot = snapshot
        ranked = snapshot.ranked_candidates
        ranked_symbols = {candidate.symbol for candidate in ranked}
        decisions = {decision.symbol: decision for decision in snapshot.decisions}
        display_candidates = tuple(
            sorted(
                (
                    decision
                    for decision in decisions.values()
                    if _scanner_classification(
                        decision,
                        ranked_symbols,
                    ) is not None
                ),
                key=lambda decision: (
                    decision.scanner_rank
                    if decision.scanner_rank is not None
                    else 999999,
                    -decision.score,
                    decision.symbol,
                ),
            )
        )
        current = {candidate.symbol for candidate in display_candidates}

        self._log_candidate_transitions(ranked, decisions)
        self._publish_experiment_candidates(decisions, cycle=cycle, now=now)

        for symbol in sorted(self._displayed_symbols - current):
            self._emit(
                now,
                cycle,
                "SCANNER_CANDIDATE_REMOVED",
                f"Removed {symbol} from the scanner ranking.",
                symbol,
                RuntimeWatchlistUpdate(symbol=symbol, subscribed=False),
            )
            self._published_display_fingerprints.pop(symbol, None)

        session = (
            snapshot.session
            if snapshot.session != "UNKNOWN"
            else scanner_session(now).value
        )
        stale_symbols = tuple(
            candidate.symbol
            for candidate in display_candidates
            if _market_data_is_stale(
                candidate, now=now, stale_after=self._stale_after,
                fallback=snapshot.timestamp,
            )
        )
        self._last_stale_symbols = stale_symbols

        display_fingerprints = {
            candidate.symbol: (
                session,
                candidate,
                candidate.symbol in stale_symbols,
                _component_freshness(
                    candidate.last_price_timestamp,
                    now=now,
                    stale_after=self._stale_after,
                )[0],
                _component_freshness(
                    candidate.quote_timestamp,
                    now=now,
                    stale_after=self._stale_after,
                )[0],
            )
            for candidate in display_candidates
        }
        fingerprint = tuple(
            (candidate.symbol, display_fingerprints[candidate.symbol])
            for candidate in display_candidates
        )
        if fingerprint == self._last_fingerprint:
            self._last_changed = False
            self._diagnostics.increment(
                "scanner_snapshots_suppressed_unchanged"
            )
            return ()

        self._last_fingerprint = fingerprint
        self._last_changed = True
        self._diagnostics.increment("scanner_snapshots_published")
        for display_rank, candidate in enumerate(display_candidates, 1):
            if (
                self._published_display_fingerprints.get(candidate.symbol)
                == display_fingerprints[candidate.symbol]
            ):
                continue
            market_timestamp = _market_timestamp(candidate, snapshot.timestamp)
            evaluation_timestamp = candidate.observed_at or snapshot.timestamp
            market_age = max(timedelta(), now - market_timestamp)
            evaluation_age = max(timedelta(), now - evaluation_timestamp)
            last_freshness, last_age = _component_freshness(
                candidate.last_price_timestamp,
                now=now,
                stale_after=self._stale_after,
            )
            quote_freshness, quote_age = _component_freshness(
                candidate.quote_timestamp,
                now=now,
                stale_after=self._stale_after,
            )
            stale = candidate.symbol in stale_symbols
            metadata = (
                ("scanner_rank", str(candidate.scanner_rank or display_rank)),
                ("scanner_score", str(candidate.score)),
                (
                    "scanner_relative_volume",
                    _decimal(candidate.metrics.relative_volume),
                ),
                (
                    "scanner_dollar_volume",
                    _decimal(candidate.metrics.dollar_volume),
                ),
                (
                    "scanner_spread",
                    "--"
                    if candidate.metrics.spread_percent is None
                    else _decimal(candidate.metrics.spread_percent),
                ),
                ("scanner_catalyst", candidate.catalyst.value),
                (
                    "scanner_catalyst_headline",
                    candidate.catalyst_headline or "--",
                ),
                ("scanner_passed_rules", ", ".join(candidate.passed_rules) or "--"),
                ("scanner_failed_rules", ", ".join(candidate.failed_rules) or "--"),
                ("scanner_freshness", "STALE" if stale else "LIVE"),
                ("scanner_market_age_ms", str(int(market_age.total_seconds() * 1000))),
                ("scanner_last_price_freshness", last_freshness),
                ("scanner_last_price_age_ms", _age_ms(last_age)),
                ("scanner_quote_freshness", quote_freshness),
                ("scanner_quote_age_ms", _age_ms(quote_age)),
                ("scanner_evaluation_age_ms", str(int(evaluation_age.total_seconds() * 1000))),
                ("scanner_market_timestamp", market_timestamp.isoformat()),
                ("scanner_evaluation_timestamp", evaluation_timestamp.isoformat()),
                (
                    "scanner_last_price_timestamp",
                    "--" if candidate.last_price_timestamp is None
                    else candidate.last_price_timestamp.isoformat(),
                ),
                (
                    "scanner_quote_timestamp",
                    "--" if candidate.quote_timestamp is None
                    else candidate.quote_timestamp.isoformat(),
                ),
                (
                    "scanner_last_price_received_timestamp",
                    "--" if candidate.last_price_received_timestamp is None
                    else candidate.last_price_received_timestamp.isoformat(),
                ),
                (
                    "scanner_quote_received_timestamp",
                    "--" if candidate.quote_received_timestamp is None
                    else candidate.quote_received_timestamp.isoformat(),
                ),
                (
                    "scanner_last_price_source_to_receive_ms",
                    _latency_ms(
                        candidate.last_price_timestamp,
                        candidate.last_price_received_timestamp,
                    ),
                ),
                (
                    "scanner_quote_source_to_receive_ms",
                    _latency_ms(
                        candidate.quote_timestamp,
                        candidate.quote_received_timestamp,
                    ),
                ),
                ("scanner_session", session),
                (
                    "scanner_classification",
                    _scanner_classification(
                        candidate,
                        ranked_symbols,
                    ) or "--",
                ),
                (
                    "technical_qualifies_without_catalyst",
                    str(candidate.technical_qualifies_without_catalyst).lower(),
                ),
                ("experiment_cohorts", ", ".join(candidate.cohort_flags) or "--"),
            )
            classification = _scanner_classification(
                candidate,
                ranked_symbols,
            )
            if classification == "QUALIFYING":
                event_type = "candidate_qualified"
                message = (
                    f"Scanner ranked {candidate.symbol} at "
                    f"{candidate.scanner_rank or display_rank}."
                )
            elif classification == "WATCHING":
                event_type = "SCANNER_CANDIDATE_WATCHING"
                message = (
                    f"Scanner is watching {candidate.symbol}; "
                    "technical criteria qualify without catalyst."
                )
            elif classification == "NEAR MISS":
                event_type = "SCANNER_CANDIDATE_NEAR_MISS"
                message = (
                    f"Scanner near miss {candidate.symbol}; "
                    f"failed {candidate.technical_failed_rules[0]}."
                )
            else:
                raise RuntimeError(
                    "display candidate has no scanner classification"
                )
            self._emit(
                now,
                cycle,
                event_type,
                message,
                candidate.symbol,
                RuntimeWatchlistUpdate(
                    symbol=candidate.symbol,
                    subscribed=True,
                    quote=RuntimeWatchlistQuote(
                        timestamp=market_timestamp,
                        latest_price=candidate.price,
                        change_percent=candidate.metrics.percentage_change,
                        volume=(
                            int(candidate.current_volume)
                            if candidate.current_volume is not None
                            and candidate.current_volume
                            == candidate.current_volume.to_integral_value()
                            else None
                        ),
                        stale=stale,
                    ),
                    market_status=session,
                    metadata=metadata,
                ),
            )
            _LOGGER.info(
                "event_type=%s symbol=%s rank=%d score=%d",
                (
                    "candidate_qualified"
                    if candidate.symbol in ranked_symbols
                    else "candidate_watching"
                ),
                candidate.symbol,
                candidate.scanner_rank or display_rank,
                candidate.score,
            )

        self._displayed_symbols = current
        self._published_display_fingerprints = display_fingerprints
        self._published_symbols = ranked_symbols
        self._published_decisions = {
            candidate.symbol: candidate for candidate in ranked
        }
        self._last_decisions = decisions
        return stale_symbols

    def _publish_experiment_candidates(
        self,
        decisions: dict[str, ScannerDecision],
        *,
        cycle: int,
        now: datetime,
    ) -> None:
        for symbol, decision in sorted(decisions.items()):
            if not decision.technical_qualifies_without_catalyst:
                continue
            fingerprint = (
                decision.timestamp,
                decision.qualified,
                decision.catalyst_status,
                decision.cohort_flags,
                decision.failed_rules,
            )
            if self._published_experiments.get(symbol) == fingerprint:
                continue
            self._published_experiments[symbol] = fingerprint
            outcome = "pending"
            message = (
                f"Experiment candidate {symbol}: normal_qualify="
                f"{str(decision.qualified).lower()} technical_only_qualify=true "
                f"catalyst={decision.catalyst_status.value} cohorts="
                f"{','.join(decision.cohort_flags) or 'none'} outcome={outcome}."
            )
            self._sink(
                PaperRuntimeEvent(
                    sequence=self._sequence(), timestamp=now,
                    event_type="EXPERIMENT_CANDIDATE", message=message,
                    cycle=cycle, symbol=symbol, source=self._source,
                )
            )

    def _log_candidate_transitions(
        self,
        ranked: tuple[ScannerDecision, ...],
        decisions: dict[str, ScannerDecision],
    ) -> None:
        current = {candidate.symbol for candidate in ranked}
        entered = current - self._published_symbols
        exited = self._published_symbols - current

        for symbol in sorted(entered):
            prior = self._last_decisions.get(symbol)
            candidate = decisions.get(symbol) or next(
                item for item in ranked if item.symbol == symbol
            )
            _LOGGER.info(
                "event_type=candidate_entered symbol=%s "
                "previous_value=%s current_value=%s",
                symbol,
                "missing" if prior is None else "rejected",
                f"qualified(score={candidate.score})",
            )

        for symbol in sorted(exited):
            previous = self._published_decisions[symbol]
            current_decision = decisions.get(symbol)
            if current_decision is None:
                failed_rule = "missing_decision"
                previous_value = "present"
                current_value = "missing"
            elif current_decision.failed_rules:
                failed_rule = current_decision.failed_rules[0]
                previous_value = _rule_value(previous, failed_rule)
                current_value = _rule_value(current_decision, failed_rule)
            else:
                failed_rule = "rank_limit"
                previous_value = f"score={previous.score}"
                current_value = f"score={current_decision.score}"
            _LOGGER.info(
                "event_type=candidate_exited symbol=%s failed_rule_on_exit=%s "
                "previous_value=%s current_value=%s",
                symbol,
                failed_rule,
                previous_value,
                current_value,
            )

    def _emit(
        self,
        timestamp: datetime,
        cycle: int,
        event_type: str,
        message: str,
        symbol: str,
        update: RuntimeWatchlistUpdate,
    ) -> None:
        self._sink(
            PaperRuntimeEvent(
                sequence=self._sequence(),
                timestamp=timestamp,
                event_type=event_type,
                message=message,
                cycle=cycle,
                symbol=symbol,
                source=self._source,
                watchlist=update,
            )
        )


def _decimal(value: Decimal) -> str:
    return format(value, "f")


def _market_timestamp(decision: ScannerDecision, fallback: datetime) -> datetime:
    timestamps = tuple(
        value for value in (
            decision.last_price_timestamp,
            decision.quote_timestamp,
        ) if value is not None
    )
    return min(timestamps) if timestamps else decision.timestamp or fallback


def _market_data_is_stale(
    decision: ScannerDecision, *, now: datetime,
    stale_after: timedelta, fallback: datetime,
) -> bool:
    if (
        decision.last_price_timestamp is None
        and decision.quote_timestamp is not None
    ) or (
        decision.quote_timestamp is None
        and decision.last_price_timestamp is not None
    ):
        return True
    return now - _market_timestamp(decision, fallback) > stale_after


def _component_freshness(
    timestamp: datetime | None, *, now: datetime, stale_after: timedelta,
) -> tuple[str, timedelta | None]:
    if timestamp is None:
        return "MISSING", None
    age = max(timedelta(), now - timestamp)
    return ("STALE" if age > stale_after else "LIVE"), age


def _age_ms(value: timedelta | None) -> str:
    return "--" if value is None else str(int(value.total_seconds() * 1000))


def _latency_ms(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "--"
    return str(int(max(timedelta(), end - start).total_seconds() * 1000))


def _rule_value(decision: ScannerDecision, rule: str) -> str:
    values = dict(decision.diagnostic_rule_values)
    if rule in values:
        return values[rule]
    if rule == "percentage_change":
        return _decimal(decision.metrics.percentage_change)
    if rule == "relative_volume":
        return _decimal(decision.metrics.relative_volume)
    if rule == "dollar_volume":
        return _decimal(decision.metrics.dollar_volume)
    if rule == "spread":
        value = decision.metrics.spread_percent
        return "missing" if value is None else _decimal(value)
    if rule == "price_range":
        return "missing" if decision.price is None else _decimal(decision.price)
    if rule == "news_catalyst":
        return decision.catalyst_status.value
    return "unavailable"


__all__ = ["ScannerSnapshotPublisher"]

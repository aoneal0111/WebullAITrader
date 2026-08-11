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
        self._published_decisions: dict[str, ScannerDecision] = {}
        self._last_decisions: dict[str, ScannerDecision] = {}
        self._last_fingerprint: object | None = None
        self._last_changed = False
        self._diagnostics = diagnostics

    @property
    def last_changed(self) -> bool:
        return self._last_changed

    def publish(
        self,
        snapshot: ScannerSnapshot,
        *,
        cycle: int,
        now: datetime,
    ) -> tuple[str, ...]:
        ranked = snapshot.ranked_candidates
        current = {candidate.symbol for candidate in ranked}
        decisions = {decision.symbol: decision for decision in snapshot.decisions}

        self._log_candidate_transitions(ranked, decisions)

        for symbol in sorted(self._published_symbols - current):
            self._emit(
                now,
                cycle,
                "SCANNER_CANDIDATE_REMOVED",
                f"Removed {symbol} from the scanner ranking.",
                symbol,
                RuntimeWatchlistUpdate(symbol=symbol, subscribed=False),
            )

        session = (
            snapshot.session
            if snapshot.session != "UNKNOWN"
            else scanner_session(now).value
        )
        fingerprint = (
            session,
            ranked,
            tuple(
                now - (candidate.timestamp or snapshot.timestamp)
                > self._stale_after
                for candidate in ranked
            ),
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
        stale_symbols: list[str] = []
        for rank, candidate in enumerate(ranked, 1):
            observed_at = candidate.timestamp or snapshot.timestamp
            stale = now - observed_at > self._stale_after
            if stale:
                stale_symbols.append(candidate.symbol)
            metadata = (
                ("scanner_rank", str(rank)),
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
                ("scanner_session", session),
            )
            self._emit(
                now,
                cycle,
                "candidate_qualified",
                f"Scanner ranked {candidate.symbol} at {rank}.",
                candidate.symbol,
                RuntimeWatchlistUpdate(
                    symbol=candidate.symbol,
                    subscribed=True,
                    quote=RuntimeWatchlistQuote(
                        timestamp=observed_at,
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
                "event_type=candidate_qualified symbol=%s rank=%d score=%d",
                candidate.symbol,
                rank,
                candidate.score,
            )

        self._published_symbols = current
        self._published_decisions = {
            candidate.symbol: candidate for candidate in ranked
        }
        self._last_decisions = decisions
        return tuple(stale_symbols)

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

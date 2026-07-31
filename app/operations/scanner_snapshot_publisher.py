"""Project immutable scanner snapshots onto the existing desktop watchlist."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

from app.live_scanner.session import scanner_session
from app.operations.runtime import (
    PaperRuntimeEvent,
    RuntimeWatchlistQuote,
    RuntimeWatchlistUpdate,
)
from app.realtime_scanner.models import ScannerSnapshot


class ScannerSnapshotPublisher:
    def __init__(
        self,
        event_sink: Callable[[PaperRuntimeEvent], None],
        sequence_source: Callable[[], int],
        *,
        source: str,
        stale_after: timedelta,
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

    def publish(
        self,
        snapshot: ScannerSnapshot,
        *,
        cycle: int,
        now: datetime,
    ) -> tuple[str, ...]:
        ranked = snapshot.ranked_candidates
        current = {candidate.symbol for candidate in ranked}

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

        self._published_symbols = current
        return tuple(stale_symbols)

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


__all__ = ["ScannerSnapshotPublisher"]

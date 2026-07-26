from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


class ScannerCoordinator(Protocol):
    def run_available(self, *, maximum_events: int | None = None) -> Any: ...

    def snapshot(self, *, limit: int = 25) -> Any: ...


class RankedCandidate(Protocol):
    symbol: str


SnapshotResolver = Callable[[str, datetime], Any | None]


@dataclass(frozen=True, slots=True)
class ScannerRuntimeCycle:
    timestamp: datetime
    events_read: int
    decisions_created: int
    ranked_symbols: tuple[str, ...]
    resolved_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_aware(self.timestamp)
        if self.events_read < 0 or self.decisions_created < 0:
            raise ValueError("scanner runtime counters cannot be negative")


class LiveScannerSnapshotSource:
    """Adapt a live scanner coordinator to the paper runtime snapshot source.

    Every invocation drains a bounded event batch, takes one immutable scanner
    snapshot, and resolves ranked symbols into strategy-ready snapshots. Missing
    market history is explicit and does not manufacture indicator values.
    """

    def __init__(
        self,
        coordinator: ScannerCoordinator,
        snapshot_resolver: SnapshotResolver,
        *,
        candidate_limit: int = 25,
        maximum_events_per_cycle: int = 1000,
        cycle_sink: Callable[[ScannerRuntimeCycle], None] | None = None,
        snapshot_sink: Callable[[Any], None] | None = None,
    ) -> None:
        if candidate_limit < 1:
            raise ValueError("candidate limit must be positive")
        if maximum_events_per_cycle < 1:
            raise ValueError("maximum events per cycle must be positive")
        self._coordinator = coordinator
        self._snapshot_resolver = snapshot_resolver
        self._candidate_limit = candidate_limit
        self._maximum_events_per_cycle = maximum_events_per_cycle
        self._cycle_sink = cycle_sink
        self._snapshot_sink = snapshot_sink
        self._last_cycle: ScannerRuntimeCycle | None = None

    @property
    def last_cycle(self) -> ScannerRuntimeCycle | None:
        return self._last_cycle

    def __call__(self, timestamp: datetime) -> Iterable[Any]:
        _require_aware(timestamp)
        stream_cycle = self._coordinator.run_available(
            maximum_events=self._maximum_events_per_cycle,
        )
        scanner_snapshot = self._coordinator.snapshot(
            limit=self._candidate_limit,
        )

        ranked_symbols = _ranked_symbols(scanner_snapshot.ranked_candidates)
        resolved: list[Any] = []
        resolved_symbols: list[str] = []
        missing_symbols: list[str] = []

        for symbol in ranked_symbols:
            snapshot = self._snapshot_resolver(symbol, timestamp)
            if snapshot is None:
                missing_symbols.append(symbol)
                continue
            resolved_symbol = _normalize_symbol(getattr(snapshot, "symbol", None))
            if resolved_symbol != symbol:
                raise ValueError(
                    "resolved snapshot symbol does not match ranked candidate"
                )
            resolved.append(snapshot)
            resolved_symbols.append(symbol)

        cycle = ScannerRuntimeCycle(
            timestamp=timestamp,
            events_read=int(stream_cycle.events_read),
            decisions_created=int(stream_cycle.decisions_created),
            ranked_symbols=ranked_symbols,
            resolved_symbols=tuple(resolved_symbols),
            missing_symbols=tuple(missing_symbols),
        )
        self._last_cycle = cycle
        if self._cycle_sink is not None:
            self._cycle_sink(cycle)
        if self._snapshot_sink is not None:
            self._snapshot_sink(scanner_snapshot)
        return tuple(resolved)


def _ranked_symbols(candidates: Iterable[RankedCandidate]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        symbol = _normalize_symbol(candidate.symbol)
        if symbol in seen:
            raise ValueError("scanner ranked candidates contain duplicate symbols")
        seen.add(symbol)
        result.append(symbol)
    return tuple(result)


def _normalize_symbol(value: Any) -> str:
    if value is None:
        raise ValueError("scanner candidate symbol is required")
    symbol = str(value).strip().upper()
    if not symbol:
        raise ValueError("scanner candidate symbol is required")
    return symbol


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scanner runtime timestamps must be timezone-aware")

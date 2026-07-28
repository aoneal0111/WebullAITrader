from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.event_store import EventStoreSnapshot, EventStoreStatus
from app.operations_core import (
    DecisionsUpdated,
    PaperRuntimeUpdated,
    PositionsUpdated,
    TradeLifecycleUpdated,
)


@dataclass(frozen=True, slots=True)
class HistoricalTrade:
    session_id: str
    symbol: str
    strategy_version: str
    decision: str
    committee_outcome: str
    lifecycle_phase: str
    opened_at: datetime | None
    closed_at: datetime
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class AnalyticsDataset:
    trades: tuple[HistoricalTrade, ...] = ()
    lifecycle_counts: tuple[tuple[str, int], ...] = ()
    equity: tuple[tuple[datetime, Decimal], ...] = ()
    exposures: tuple[Decimal, ...] = ()
    largest_position: Decimal = Decimal("0")
    symbols: tuple[str, ...] = ()
    strategies: tuple[str, ...] = ()


class AnalyticsRepository:
    """Translate one immutable Event Store snapshot into analytics facts."""

    def load(
        self,
        snapshot: EventStoreSnapshot,
        *,
        symbol: str | None = None,
        strategy_version: str | None = None,
    ) -> AnalyticsDataset:
        if not isinstance(snapshot, EventStoreSnapshot):
            raise TypeError("snapshot must be EventStoreSnapshot")
        if snapshot.status is EventStoreStatus.ERROR:
            raise ValueError("event store snapshot is in an error state")
        selected_symbol = _optional_text(symbol, "symbol")
        if selected_symbol is not None:
            selected_symbol = selected_symbol.upper()
        selected_strategy = _optional_text(
            strategy_version,
            "strategy_version",
        )

        sessions = {
            session.session_id: session
            for session in snapshot.sessions
        }
        decisions: dict[tuple[str, str], tuple[str, str]] = {}
        committees: dict[tuple[str, str], str] = {}
        opened: dict[tuple[str, str], datetime] = {}
        trades: list[HistoricalTrade] = []
        phase_counts: dict[str, int] = {}
        equity: list[tuple[datetime, Decimal]] = []
        exposures: list[Decimal] = []
        largest_position = Decimal("0")

        for indexed in snapshot.all_events:
            event = indexed.event
            session_id = indexed.session_id
            if isinstance(event, DecisionsUpdated):
                for decision in event.decisions:
                    decisions[(session_id, decision.symbol)] = (
                        decision.action,
                        decision.strategy_version,
                    )
            elif isinstance(event, TradeLifecycleUpdated):
                key = (session_id, event.symbol)
                phase_counts[event.phase] = phase_counts.get(event.phase, 0) + 1
                if event.phase == "COMMITTEE":
                    committees[key] = event.title
                if event.phase == "POSITION_OPEN":
                    opened[key] = event.occurred_at
                if event.phase in {"POSITION_CLOSE", "EXIT"}:
                    decision, strategy = decisions.get(
                        key,
                        (
                            "UNKNOWN",
                            sessions.get(session_id).strategy_version
                            if session_id in sessions
                            else "UNKNOWN",
                        ),
                    )
                    trades.append(
                        HistoricalTrade(
                            session_id=session_id,
                            symbol=event.symbol,
                            strategy_version=strategy,
                            decision=decision,
                            committee_outcome=committees.get(
                                key,
                                "UNKNOWN",
                            ),
                            lifecycle_phase=event.phase,
                            opened_at=opened.pop(key, None),
                            closed_at=event.occurred_at,
                            realized_pnl=event.realized_pnl or Decimal("0"),
                        )
                    )
            elif isinstance(event, PaperRuntimeUpdated):
                equity.append(
                    (
                        event.snapshot.timestamp,
                        event.snapshot.current_equity,
                    )
                )
            elif isinstance(event, PositionsUpdated):
                values = tuple(
                    abs(_decimal(position.market_value))
                    for position in event.positions
                )
                exposures.append(sum(values, Decimal("0")))
                if values:
                    largest_position = max(largest_position, *values)

        filtered = tuple(
            trade
            for trade in trades
            if (
                selected_symbol is None
                or trade.symbol == selected_symbol
            )
            and (
                selected_strategy is None
                or trade.strategy_version == selected_strategy
            )
        )
        return AnalyticsDataset(
            trades=filtered,
            lifecycle_counts=tuple(sorted(phase_counts.items())),
            equity=tuple(sorted(equity, key=lambda item: item[0])),
            exposures=tuple(exposures),
            largest_position=largest_position,
            symbols=tuple(sorted({trade.symbol for trade in trades})),
            strategies=tuple(
                sorted({trade.strategy_version for trade in trades})
            ),
        )

    def close(self) -> None:
        return None


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("position market value must be decimal") from exc
    if not result.is_finite():
        raise ValueError("position market value must be finite")
    return result


def _optional_text(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be stripped non-empty text or None")
    return value

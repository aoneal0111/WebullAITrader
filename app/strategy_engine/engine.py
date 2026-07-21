from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Protocol

from app.strategy.scoring import StrategyAction, StrategyScore, score_snapshot
from app.strategy_engine.models import (
    StrategyDecision,
    StrategyDecisionAction,
    StrategyEngineConfig,
    StrategyPosition,
)


class SnapshotLike(Protocol):
    symbol: str


ScoreFunction = Callable[[SnapshotLike], StrategyScore]


class StrategyEngine:
    """
    Convert deterministic strategy scores into position-aware decisions.

    This component is advisory only. It does not choose quantities, submit
    orders, access a broker, or mutate a portfolio.
    """

    def __init__(
        self,
        config: StrategyEngineConfig | None = None,
        *,
        scorer: ScoreFunction | None = None,
        strategy_version: str = "1.0",
    ) -> None:
        self._config = config or StrategyEngineConfig()
        self._scorer = scorer or score_snapshot
        self._strategy_version = strategy_version.strip()

        if not self._strategy_version:
            raise ValueError("strategy_version is required")

        self._last_action_at: dict[str, datetime] = {}

    @property
    def config(self) -> StrategyEngineConfig:
        return self._config

    def evaluate(
        self,
        snapshot: SnapshotLike,
        position: StrategyPosition,
        *,
        timestamp: datetime,
    ) -> StrategyDecision:
        symbol = snapshot.symbol.strip().upper()

        if not symbol:
            raise ValueError("snapshot symbol is required")

        if position.symbol != symbol:
            raise ValueError(
                "snapshot and position symbols must match"
            )

        _require_aware(timestamp)

        score = self._scorer(snapshot)
        action = self._choose_action(
            symbol=symbol,
            score=score,
            position=position,
            timestamp=timestamp,
        )

        decision = StrategyDecision(
            symbol=symbol,
            action=action,
            confidence=score.confidence,
            score=Decimal(str(score.score)),
            timestamp=timestamp,
            reasons=tuple(score.reasons),
            source_action=score.action.value,
            position_quantity=position.quantity,
            strategy_version=self._strategy_version,
        )

        if decision.creates_order_intent:
            self._last_action_at[symbol] = timestamp

        return decision

    def evaluate_many(
        self,
        snapshots: tuple[SnapshotLike, ...],
        positions: dict[str, StrategyPosition],
        *,
        timestamp: datetime,
    ) -> tuple[StrategyDecision, ...]:
        _require_aware(timestamp)

        decisions: list[StrategyDecision] = []

        for snapshot in snapshots:
            symbol = snapshot.symbol.strip().upper()
            position = positions.get(
                symbol,
                StrategyPosition(symbol),
            )
            decisions.append(
                self.evaluate(
                    snapshot,
                    position,
                    timestamp=timestamp,
                )
            )

        return tuple(decisions)

    def reset_cooldown(
        self,
        symbol: str | None = None,
    ) -> None:
        if symbol is None:
            self._last_action_at.clear()
            return

        normalized = symbol.strip().upper()

        if not normalized:
            raise ValueError("symbol is required")

        self._last_action_at.pop(normalized, None)

    def _choose_action(
        self,
        *,
        symbol: str,
        score: StrategyScore,
        position: StrategyPosition,
        timestamp: datetime,
    ) -> StrategyDecisionAction:
        if self._cooldown_active(symbol, timestamp):
            return StrategyDecisionAction.IGNORE

        if position.is_flat:
            return self._flat_action(score)

        if position.is_long:
            return self._long_action(score)

        return self._short_action(score)

    def _flat_action(
        self,
        score: StrategyScore,
    ) -> StrategyDecisionAction:
        if score.confidence < self._config.entry_confidence:
            return StrategyDecisionAction.IGNORE

        if score.action is StrategyAction.BUY:
            return StrategyDecisionAction.ENTER_LONG

        if (
            score.action is StrategyAction.SELL
            and self._config.allow_short_entries
        ):
            return StrategyDecisionAction.ENTER_SHORT

        if score.action is StrategyAction.HOLD:
            return StrategyDecisionAction.HOLD

        return StrategyDecisionAction.IGNORE

    def _long_action(
        self,
        score: StrategyScore,
    ) -> StrategyDecisionAction:
        if score.action is StrategyAction.SELL:
            if score.confidence >= self._config.exit_confidence:
                return StrategyDecisionAction.EXIT_LONG

        if (
            score.action is StrategyAction.BUY
            and score.confidence >= self._config.entry_confidence
        ):
            return StrategyDecisionAction.HOLD

        return StrategyDecisionAction.HOLD

    def _short_action(
        self,
        score: StrategyScore,
    ) -> StrategyDecisionAction:
        if score.action is StrategyAction.BUY:
            if score.confidence >= self._config.exit_confidence:
                return StrategyDecisionAction.EXIT_SHORT

        if (
            score.action is StrategyAction.SELL
            and score.confidence >= self._config.entry_confidence
        ):
            return StrategyDecisionAction.HOLD

        return StrategyDecisionAction.HOLD

    def _cooldown_active(
        self,
        symbol: str,
        timestamp: datetime,
    ) -> bool:
        if self._config.cooldown_seconds == 0:
            return False

        previous = self._last_action_at.get(symbol)

        if previous is None:
            return False

        if timestamp < previous:
            raise ValueError(
                "timestamp cannot precede the previous action"
            )

        return timestamp - previous < timedelta(
            seconds=self._config.cooldown_seconds
        )


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "timestamp must be timezone-aware"
        )

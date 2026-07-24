from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.execution_coordinator.runtime_context_provider import (
    RuntimeContextInputs,
)
from app.paper_session import PaperTradingSession
from app.strategy_engine import StrategyOrderIntent


TimestampSource = Callable[
    [StrategyOrderIntent, object, PaperTradingSession],
    datetime,
]
MarketStateSource = Callable[
    [StrategyOrderIntent, object, PaperTradingSession, datetime],
    object,
]
MarketQuoteSource = Callable[
    [StrategyOrderIntent, object, PaperTradingSession, datetime],
    object,
]
GFVDecisionSource = Callable[
    [StrategyOrderIntent, object, PaperTradingSession, datetime],
    object,
]


@dataclass(frozen=True, slots=True)
class RuntimeContextConfiguration:
    """Authoritative static configuration for runtime coordination."""

    account_type: object
    risk_limits: object
    compliance_limits: object
    kill_switch: object
    execution_config: object


@dataclass(frozen=True, slots=True)
class ConfiguredRuntimeContextInputSource:
    """Compose immutable runtime context inputs from injected authorities."""

    configuration: RuntimeContextConfiguration
    timestamp_source: TimestampSource
    market_state_source: MarketStateSource
    market_quote_source: MarketQuoteSource
    gfv_decision_source: GFVDecisionSource

    def __call__(
        self,
        *,
        order_intent: StrategyOrderIntent,
        symbol: str,
        snapshot: object,
        session: PaperTradingSession,
        cycle: int,
        symbol_index: int,
    ) -> RuntimeContextInputs:
        del symbol, cycle, symbol_index

        timestamp = self.timestamp_source(
            order_intent,
            snapshot,
            session,
        )
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError(
                "runtime context timestamp must be timezone-aware"
            )

        return RuntimeContextInputs(
            account_type=self.configuration.account_type,
            timestamp=timestamp,
            market_state=self.market_state_source(
                order_intent,
                snapshot,
                session,
                timestamp,
            ),
            risk_limits=self.configuration.risk_limits,
            compliance_limits=self.configuration.compliance_limits,
            gfv_decision=self.gfv_decision_source(
                order_intent,
                snapshot,
                session,
                timestamp,
            ),
            kill_switch=self.configuration.kill_switch,
            market_quote=self.market_quote_source(
                order_intent,
                snapshot,
                session,
                timestamp,
            ),
            execution_config=self.configuration.execution_config,
        )


__all__ = [
    "ConfiguredRuntimeContextInputSource",
    "GFVDecisionSource",
    "MarketQuoteSource",
    "MarketStateSource",
    "RuntimeContextConfiguration",
    "TimestampSource",
]

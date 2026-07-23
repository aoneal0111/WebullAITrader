from __future__ import annotations

from dataclasses import dataclass

from app.paper_session import PaperTradingSession


@dataclass(frozen=True, slots=True)
class CoordinationContext:
    """
    Immutable runtime execution context required to assemble a
    CoordinationRequest.

    This object intentionally contains no business logic. It exists only
    to aggregate coordinator inputs supplied by runtime composition.
    """

    account_state: object
    market_state: object
    risk_limits: object
    compliance_limits: object
    gfv_decision: object
    kill_switch: object
    portfolio: object
    market_quote: object
    execution_config: object
    journal: object
    equity_curve: object


class CoordinationContextProvider:
    """
    Runtime dependency responsible for supplying execution context for
    CoordinationRequest construction.
    """

    def get_context(
        self,
        *,
        symbol: str,
        snapshot: object,
        session: PaperTradingSession,
        cycle: int,
        symbol_index: int,
    ) -> CoordinationContext:
        raise NotImplementedError

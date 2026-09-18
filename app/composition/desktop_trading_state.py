from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.paper_trading.command_composition import PAPER_ACCOUNT_ID
from app.portfolio_intelligence import PortfolioAccount
from app.strategies.warrior_momentum.forward_models import PaperAccountContext
from app.webull.market_data_session import utc_now


class DesktopTradingStateSources:
    """Read-only adapters from desktop projections into trading composition."""

    def __init__(
        self,
        *,
        state_store: object,
        runtime_projections: object,
        operational_configuration: object,
    ) -> None:
        self._state_store = state_store
        self._runtime_projections = runtime_projections
        self._configuration = operational_configuration

    def portfolio_account(self) -> PortfolioAccount:
        state = self._state_store.snapshot()
        account = state.broker_account
        if self._configuration.environment.value == "PAPER":
            paper_account = state.paper_account
            if paper_account is not None:
                return PortfolioAccount(
                    paper_account.campaign_id,
                    paper_account.current_equity,
                    paper_account.current_cash,
                    paper_account.buying_power,
                )
        if account is not None:
            return PortfolioAccount(
                account.account_id,
                account.equity,
                account.cash_balance,
                account.buying_power,
                account.currency,
            )
        paper = state.paper_runtime
        return PortfolioAccount(
            self._configuration.account_id or PAPER_ACCOUNT_ID,
            paper.current_equity if paper is not None else None,
            None,
            None,
        )

    def position_average_cost(self, symbol: str) -> Decimal | None:
        position = self._runtime_projections.position_projection.position_for_symbol(
            symbol
        )
        return (
            None
            if position is None
            else Decimal(position.average_cost)
        )

    def position_quantity(self, symbol: str) -> Decimal:
        position = self._runtime_projections.position_projection.position_for_symbol(
            symbol
        )
        return (
            Decimal("0")
            if position is None
            else Decimal(position.quantity)
        )

    def adaptive_position(self, symbol: str) -> tuple[Decimal, datetime]:
        position = self._runtime_projections.position_projection.position_for_symbol(
            symbol
        )
        if position is None:
            return Decimal("0"), utc_now()
        return Decimal(position.quantity), position.updated_at

    def warrior_account_context(self) -> PaperAccountContext | None:
        state = self._state_store.snapshot()
        account = state.broker_account
        paper_account = (
            state.paper_account
            if self._configuration.environment.value == "PAPER"
            else None
        )
        if paper_account is not None:
            equity = paper_account.current_equity
            buying_power = paper_account.buying_power
        elif account is not None:
            equity = getattr(account, "equity", None)
            buying_power = getattr(account, "buying_power", None)
        else:
            paper = state.paper_runtime
            equity = None if paper is None else paper.current_equity
            buying_power = equity

        if equity is None or buying_power is None:
            return None

        return PaperAccountContext(
            equity=Decimal(equity),
            buying_power=Decimal(buying_power),
            allowed_symbols=frozenset(self._configuration.allowed_symbols),
            risk_engine_approved=True,
            broker_restriction=False,
            symbol_authorization_mode=(
                self._configuration.paper_symbol_authorization_mode
            ),
        )


__all__ = ["DesktopTradingStateSources"]

"""Paper execution planning that delegates all caller-owned Atlas safeguards."""

from __future__ import annotations

from decimal import Decimal

from .configuration import RiskConfig, TradeManagementConfig
from .models import MomentumEntrySignal, PaperTradePlan
from .risk import size_position
from .trade_management import planned_exits


def prepare_paper_plan(
    signal: MomentumEntrySignal, *, account_equity: Decimal, buying_power: Decimal,
    allowed_symbols: frozenset[str], existing_exposure: Decimal = Decimal("0"),
    exposure_limit: Decimal | None = None, risk_engine_approved: bool = True,
    broker_restriction: bool = False, risk_config: RiskConfig = RiskConfig(),
    management_config: TradeManagementConfig = TradeManagementConfig(),
) -> PaperTradePlan:
    position = size_position(
        signal, account_equity=account_equity, buying_power=buying_power,
        allowed_symbols=allowed_symbols, existing_exposure=existing_exposure,
        exposure_limit=exposure_limit, risk_engine_approved=risk_engine_approved,
        broker_restriction=broker_restriction, config=risk_config,
    )
    exits = planned_exits(signal, position.shares, management_config) if position.approved else ()
    return PaperTradePlan(signal, position, exits, position.approved, False)


__all__ = ["prepare_paper_plan"]

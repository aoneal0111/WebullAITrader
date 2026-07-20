from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal

from app.compliance.models import FundingSource, PurchaseLot, SecurityType
from app.compliance.settlement_calendar import SettlementCalendar


@dataclass(frozen=True, slots=True)
class SettlementLedger:
    """Immutable purchase-lot record; it never reads brokerage balances."""

    lots: tuple[PurchaseLot, ...] = ()

    def record_purchase(
        self,
        *,
        symbol: str,
        quantity: Decimal,
        purchase_timestamp: datetime,
        funding_source: FundingSource,
        funding_settlement_date: date | None,
    ) -> "SettlementLedger":
        lot = PurchaseLot(
            symbol=symbol.strip().upper(),
            quantity=quantity,
            purchase_timestamp=purchase_timestamp,
            funding_source=funding_source,
            funding_settlement_date=funding_settlement_date,
            remaining_quantity=quantity,
        )
        return replace(self, lots=(*self.lots, lot))

    def record_purchase_funded_by_sale(
        self,
        *,
        symbol: str,
        quantity: Decimal,
        purchase_timestamp: datetime,
        funding_sale_trade_date: date,
        security_type: SecurityType,
        calendar: SettlementCalendar,
    ) -> "SettlementLedger":
        if security_type not in (SecurityType.STOCK, SecurityType.OPTION):
            raise ValueError("T+1 is supported only for applicable stocks and options")
        settlement = calendar.settlement_date(funding_sale_trade_date, settlement_days=1)
        return self.record_purchase(
            symbol=symbol,
            quantity=quantity,
            purchase_timestamp=purchase_timestamp,
            funding_source=FundingSource.UNSETTLED_SALE_PROCEEDS,
            funding_settlement_date=settlement,
        )

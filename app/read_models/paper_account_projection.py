"""Campaign-scoped authoritative local PAPER account projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock
from collections.abc import Callable, Mapping

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import OperationsBus, PaperAccountUpdated
from app.read_models.orders.models import OrdersReadModelSnapshot
from app.read_models.positions.models import PositionsReadModelSnapshot

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class PaperAccountSnapshot:
    campaign_id: str
    starting_equity: Decimal
    starting_cash: Decimal
    current_cash: Decimal
    current_equity: Decimal | None
    buying_power: Decimal | None
    open_position_market_value: Decimal | None
    gross_exposure: Decimal | None
    net_exposure: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal | None
    total_pnl: Decimal | None
    fees: Decimal
    active_position_count: int
    working_order_exposure: Decimal | None
    valuation_complete: bool


class PaperAccountProjection:
    """Fold active-campaign fills and marks into a cash-style account ledger.

    The projection intentionally has no broker authority.  It uses the
    campaign's fill stream for cash/realized P&L and the existing position and
    order projections for exposure and valuation.
    """

    def __init__(
        self,
        *,
        campaign_id: str,
        starting_cash: Decimal,
        position_projection,
        order_projection,
        bus: OperationsBus | None = None,
        buying_power_multiplier: Decimal = Decimal("1"),
        campaign_id_source: Callable[[], str | None] | None = None,
        capital_source: Callable[[], Mapping[str, Decimal] | None] | None = None,
    ) -> None:
        if not campaign_id.strip():
            raise ValueError("campaign_id is required")
        if not _nonnegative(starting_cash):
            raise ValueError("starting_cash must be a finite nonnegative Decimal")
        if not _positive(buying_power_multiplier):
            raise ValueError("buying_power_multiplier must be positive")
        self._campaign_id = campaign_id
        self._campaign_id_source = campaign_id_source
        self._capital_source = capital_source
        self._starting_cash = starting_cash
        self._multiplier = buying_power_multiplier
        self._position_projection = position_projection
        self._order_projection = order_projection
        self._bus = bus
        self._cash = starting_cash
        self._realized = ZERO
        self._fees = ZERO
        self._has_fills = False
        capital = self._capital()
        if capital is not None:
            self._starting_cash = capital["starting_cash"]
            self._multiplier = capital["buying_power_multiplier"]
            self._cash = self._starting_cash
        self._lock = RLock()
        self._snapshot = self._build()
        self._publish()

    @property
    def snapshot(self) -> PaperAccountSnapshot:
        with self._lock:
            return self._snapshot

    def __call__(self, event: PaperRuntimeEvent) -> None:
        if not isinstance(event, PaperRuntimeEvent):
            raise TypeError("event must be a PaperRuntimeEvent")
        with self._lock:
            if event.fill is not None:
                self._has_fills = True
                fill = event.fill
                notional = fill.quantity * fill.fill_price
                if fill.side == "BUY":
                    self._cash -= notional
                elif fill.side == "SELL":
                    self._cash += notional
                else:
                    raise ValueError("unsupported PAPER fill side")
                self._realized += fill.realized_pnl
            self._snapshot = self._build()
        self._publish(event.timestamp)

    def refresh(self) -> None:
        """Refresh campaign capital after the durable campaign is composed."""
        with self._lock:
            if not self._has_fills:
                capital = self._capital()
                if capital is not None:
                    self._starting_cash = capital["starting_cash"]
                    self._multiplier = capital["buying_power_multiplier"]
                    self._cash = self._starting_cash
            self._snapshot = self._build()
        self._publish()

    def _publish(self, occurred_at=None) -> None:
        if self._bus is not None:
            self._bus.publish(
                PaperAccountUpdated(
                    occurred_at=(occurred_at or datetime.now(timezone.utc)),
                    source="paper-account-projection",
                    account=self._snapshot,
                )
            )

    def _build(self) -> PaperAccountSnapshot:
        capital = self._capital()
        starting_equity = (
            capital["starting_equity"] if capital is not None
            else self._starting_cash
        )
        positions = self._position_projection.snapshot.positions
        orders = self._order_projection.snapshot.orders
        open_positions = tuple(
            item for item in positions if Decimal(item.quantity) != ZERO
        )
        known_market_values = [
            Decimal(item.market_value) for item in open_positions
            if item.market_value is not None
        ]
        valuation_complete = len(known_market_values) == len(open_positions)
        market_value = sum(known_market_values, ZERO) if valuation_complete else None
        gross = (
            sum((abs(value) for value in known_market_values), ZERO)
            if valuation_complete else None
        )
        net = (
            sum((value for value in known_market_values), ZERO)
            if valuation_complete else None
        )
        unrealized_values = [
            Decimal(item.unrealized_gain_loss)
            for item in open_positions
            if item.unrealized_gain_loss is not None
        ]
        unrealized = (
            sum(unrealized_values, ZERO)
            if valuation_complete and len(unrealized_values) == len(open_positions)
            else None
        )
        equity = self._cash + market_value if market_value is not None else None
        total = equity - starting_equity if equity is not None else None
        reserved = ZERO
        reservation_complete = True
        for order in orders:
            status = order.status.strip().upper().replace(" ", "_")
            if status not in {"ACCEPTED", "NEW", "WORKING", "SUBMITTED", "PARTIALLY_FILLED", "PENDING"}:
                continue
            if order.side.strip().upper() != "BUY":
                continue
            remaining = Decimal(order.remaining_quantity or order.quantity)
            if order.limit_price is None:
                reservation_complete = False
            else:
                reserved += remaining * Decimal(order.limit_price)
        buying_power = (
            self._cash * self._multiplier - reserved
            if reservation_complete else None
        )
        return PaperAccountSnapshot(
            campaign_id=(
                self._campaign_id_source() or self._campaign_id
                if self._campaign_id_source is not None
                else self._campaign_id
            ),
            starting_equity=starting_equity,
            starting_cash=self._starting_cash,
            current_cash=self._cash,
            current_equity=equity,
            buying_power=buying_power,
            open_position_market_value=market_value,
            gross_exposure=gross,
            net_exposure=net,
            realized_pnl=self._realized,
            unrealized_pnl=unrealized,
            total_pnl=total,
            fees=self._fees,
            active_position_count=len(open_positions),
            working_order_exposure=reserved if reservation_complete else None,
            valuation_complete=valuation_complete,
        )

    def _capital(self) -> Mapping[str, Decimal] | None:
        if self._capital_source is None:
            return None
        value = self._capital_source()
        if value is None:
            return None
        return {
            "starting_equity": Decimal(value["starting_equity"]),
            "starting_cash": Decimal(value["starting_cash"]),
            "buying_power_multiplier": Decimal(value["buying_power_multiplier"]),
        }


def _positive(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > ZERO


def _nonnegative(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= ZERO


__all__ = ["PaperAccountProjection", "PaperAccountSnapshot"]

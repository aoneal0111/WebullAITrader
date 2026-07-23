from datetime import datetime
from typing import Protocol, runtime_checkable

from app.paper_trading.order_models import PaperOrder


@runtime_checkable
class PaperOrderBookInterface(Protocol):
    def submit(self, order: PaperOrder) -> PaperOrder: ...

    def update(self, order: PaperOrder) -> PaperOrder: ...

    def get(self, order_id: str) -> PaperOrder: ...

    def contains(self, order_id: str) -> bool: ...

    def cancel(
        self,
        order_id: str,
        *,
        at: datetime | None = None,
    ) -> PaperOrder: ...

    def expire_day_orders(
        self,
        *,
        at: datetime | None = None,
    ) -> tuple[PaperOrder, ...]: ...

    def open_orders(self) -> tuple[PaperOrder, ...]: ...

    def open_orders_for_symbol(
        self,
        symbol: str,
    ) -> tuple[PaperOrder, ...]: ...

    def terminal_orders(self) -> tuple[PaperOrder, ...]: ...

    def history(self) -> tuple[PaperOrder, ...]: ...


__all__ = ("PaperOrderBookInterface",)

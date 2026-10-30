"""Observe new canonical native BUY fills; never submit or manage orders."""
from datetime import UTC, datetime
from threading import Lock


class NativeFillFunnel:
    def __init__(self, order_source, sink, *, started_at=None):
        self.order_source = order_source
        self.sink = sink
        self.started_at = started_at or datetime.now(UTC)
        self._seen = set()
        self._lock = Lock()

    def observe(self, event):
        # Diagnostics must never interrupt persistence, projections or exits.
        try:
            if event.event_type not in {'ORDER_FILLED', 'ORDER_PARTIALLY_FILLED'}:
                return
            fill = event.fill
            if (fill is None or fill.side != 'BUY' or fill.quantity <= 0
                    or fill.timestamp < self.started_at or event.order is None):
                return
            order = self.order_source(event.order.order_id)
            metadata = order.request.metadata
            owner = metadata.get('strategy_owner')
            if (owner not in {'WARRIOR_MOMENTUM', 'QUICK_SCALPER'}
                    or order.request.side.value != 'BUY' or order.symbol != fill.symbol):
                return
            # Only facts already in the canonical order book count. Restored
            # history and duplicate gateway notifications must not inflate it.
            if not any(f.fill_id == fill.request_id and f.quantity == fill.quantity
                       and f.price == fill.fill_price for f in order.fills):
                return
            identity = (order.order_id, fill.request_id)
            with self._lock:
                if identity in self._seen or len(self._seen) >= 8192:
                    return
                self.sink(symbol=order.symbol,
                    stage='FILL' if event.event_type == 'ORDER_FILLED' else 'PARTIAL_FILL',
                    outcome='ACCEPTED', timestamp=fill.timestamp,
                    engine='WARRIOR_MOMENTUM_V1' if owner == 'WARRIOR_MOMENTUM' else owner,
                    lifecycle_id=order.request.strategy_lifecycle_id,
                    order_id=order.order_id, fill_id=fill.request_id)
                self._seen.add(identity)
        except Exception:
            return

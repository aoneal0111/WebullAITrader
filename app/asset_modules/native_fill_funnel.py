"""Observe new canonical native fills; never submit or manage orders."""
from datetime import UTC, datetime
from threading import Lock


class NativeFillFunnel:
    def __init__(self, order_source, sink, *, started_at=None, evidence_sink=None):
        self.order_source = order_source
        self.sink = sink
        self.started_at = started_at or datetime.now(UTC)
        self.evidence_sink = evidence_sink
        self._seen = set()
        self._lock = Lock()

    def observe(self, event):
        # Diagnostics must never interrupt persistence, projections or exits.
        try:
            if event.event_type not in {'ORDER_FILLED', 'ORDER_PARTIALLY_FILLED'}:
                return
            fill = event.fill
            if (fill is None or fill.side not in {'BUY','SELL'} or fill.quantity <= 0
                    or fill.timestamp < self.started_at or event.order is None):
                return
            order = self.order_source(event.order.order_id)
            metadata = order.request.metadata
            owner = metadata.get('strategy_owner')
            if (owner not in {'WARRIOR_MOMENTUM', 'QUICK_SCALPER'}
                    or order.request.side.value != fill.side or order.symbol != fill.symbol):
                return
            # Only facts already in the canonical order book count. Restored
            # history and duplicate gateway notifications must not inflate it.
            if not any(f.fill_id == fill.request_id and f.quantity == fill.quantity
                       and f.price == fill.fill_price for f in order.fills):
                return
            identity = (order.order_id, fill.request_id)
            stage = ('FILL' if event.event_type == 'ORDER_FILLED' else 'PARTIAL_FILL') if fill.side=='BUY' else 'EXIT_FILL'
            details = {}
            if fill.side=='SELL':
                request=order.request
                reference=request.stop_price if request.order_type.value=='STOP' else request.limit_price
                details=dict(exit_reason=request.execution_reason,exit_order_type=request.order_type.value,
                    exit_reference_price=reference,fill_price=fill.fill_price,
                    adverse_from_reference=(None if reference is None else max(0,reference-fill.fill_price)))
            with self._lock:
                if identity in self._seen or len(self._seen) >= 8192:
                    return
                self.sink(symbol=order.symbol,
                    stage=stage,
                    outcome='ACCEPTED', timestamp=fill.timestamp,
                    engine='WARRIOR_MOMENTUM_V1' if owner == 'WARRIOR_MOMENTUM' else owner,
                    lifecycle_id=order.request.strategy_lifecycle_id,
                    order_id=order.order_id, fill_id=fill.request_id,**details)
                self._seen.add(identity)
                if fill.side=='SELL' and self.evidence_sink is not None:
                    self.evidence_sink('EXIT_FILL',dict(symbol=order.symbol,engine=owner,
                        lifecycle_id=order.request.strategy_lifecycle_id,order_id=order.order_id,
                        fill_id=fill.request_id,timestamp=fill.timestamp,**details))
        except Exception:
            return

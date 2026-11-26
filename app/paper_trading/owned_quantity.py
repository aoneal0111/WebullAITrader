"""Canonical, lifecycle-owned PAPER fills; presentation projections may lag."""
from decimal import Decimal


def scalper_open_quantity(orders, symbol, lifecycle_id):
    """Return proven whole-lifecycle quantity, or None for non-Scalper paths.

    Ambiguous contributors fail closed. Historical flat lots do not participate.
    No inferred broker inventory, no position creation, no ownership transfer.
    """
    if not lifecycle_id or not lifecycle_id.startswith('QUICK_SCALPER|'):
        return None
    lots={};owner=set();found=False
    for order in orders:
        if order.symbol!=symbol:continue
        identity=order.request.strategy_lifecycle_id
        if identity==lifecycle_id:
            found=True;owner.add(order.request.metadata.get('strategy_owner'))
        delta=order.filled_quantity*(1 if order.request.side.value=='BUY' else -1)
        lots[identity]=lots.get(identity,Decimal(0))+delta
    if not found or owner!={'QUICK_SCALPER'}:return Decimal(0)
    positive={k:v for k,v in lots.items() if v>0}
    if set(positive)!={lifecycle_id} or any(v<0 for v in lots.values()):return Decimal(0)
    return positive[lifecycle_id]

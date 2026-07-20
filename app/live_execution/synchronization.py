from __future__ import annotations

from app.live_execution.events import ExecutionEventLog, ExecutionEventType, append_event
from app.live_execution.models import LocalPortfolioState, SynchronizationDifference, SynchronizationReport


def synchronize(broker, local: LocalPortfolioState, log: ExecutionEventLog, timestamp):
    orders = tuple(sorted(broker.get_orders(), key=lambda item: item.client_order_id))
    positions = tuple(sorted(broker.get_positions(), key=lambda item: item.symbol))
    cash = broker.get_cash()
    differences = []
    local_orders = {item.request.client_order_id: item for item in local.orders}
    broker_orders = {item.client_order_id: item for item in orders}
    for key in sorted(set(local_orders) | set(broker_orders)):
        left, right = local_orders.get(key), broker_orders.get(key)
        if left is None or right is None:
            differences.append(SynchronizationDifference("ORDER", key, "presence", "missing" if left is None else "present", "missing" if right is None else "present"))
            continue
        for field, lv, rv in (("status", left.status.value, right.status.value),
                              ("filled_quantity", str(left.filled_quantity), str(right.filled_quantity))):
            if lv != rv: differences.append(SynchronizationDifference("ORDER", key, field, lv, rv))
    _compare_positions(local.positions, positions, differences)
    if local.cash is None:
        differences.append(SynchronizationDifference("CASH", cash.currency, "presence", "missing", "present"))
    else:
        for field in ("settled_cash", "unsettled_cash", "currency"):
            lv, rv = getattr(local.cash, field), getattr(cash, field)
            if lv != rv: differences.append(SynchronizationDifference("CASH", cash.currency, field, _string(lv), _string(rv)))
    ordered = tuple(sorted(differences, key=lambda item: (item.category, item.key, item.field)))
    for item in ordered:
        details = tuple(sorted((("category", item.category), ("field", item.field))))
        if not any(event.event_type is ExecutionEventType.SYNCHRONIZATION_MISMATCH
                   and event.request_id == item.key and event.details == details for event in log.events):
            log = append_event(log, ExecutionEventType.SYNCHRONIZATION_MISMATCH, item.key, timestamp, details)
    return SynchronizationReport(ordered, orders, positions, cash, local), log


def _compare_positions(local, broker, differences):
    left, right = {item.symbol: item for item in local}, {item.symbol: item for item in broker}
    for key in sorted(set(left) | set(right)):
        if key not in left or key not in right:
            differences.append(SynchronizationDifference("POSITION", key, "presence", "missing" if key not in left else "present", "missing" if key not in right else "present")); continue
        for field in ("quantity", "average_price", "market_value"):
            lv, rv = getattr(left[key], field), getattr(right[key], field)
            if lv != rv: differences.append(SynchronizationDifference("POSITION", key, field, _string(lv), _string(rv)))


def _string(value): return None if value is None else str(value)

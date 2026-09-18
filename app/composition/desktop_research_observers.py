from __future__ import annotations

from app.adaptive_entry_research import AdaptiveWorkingEntryObserver
from app.entry_opportunity_value import EntryOpportunityValueRuntimeObserver
from app.webull.market_data_session import utc_now


def create_entry_opportunity_value_observer(
    *,
    operational_configuration: object,
    trade_intelligence_observer: object,
    paper_order_book: object | None,
) -> EntryOpportunityValueRuntimeObserver:
    """Build advisory entry-value research outside the trading composition root."""

    def order_correlation(lifecycle_id: str) -> dict[str, object] | None:
        if paper_order_book is None:
            return None
        try:
            order = next(
                (
                    item
                    for item in reversed(paper_order_book.history())
                    if item.request.strategy_lifecycle_id == lifecycle_id
                ),
                None,
            )
        except Exception:
            return None
        if order is None:
            return None
        return {
            "order_id": order.order_id,
            "client_order_id": order.request.client_order_id,
        }

    return EntryOpportunityValueRuntimeObserver(
        enabled=(
            operational_configuration.entry_opportunity_value_enabled
            and operational_configuration.warrior_forward_paper_enabled
        ),
        environment=operational_configuration.environment.value,
        path=operational_configuration.entry_opportunity_value_path,
        capacity=operational_configuration.entry_opportunity_value_queue_capacity,
        clock=utc_now,
        research_context_source=(
            trade_intelligence_observer.entry_opportunity_context
        ),
        order_correlation_source=order_correlation,
    )


def create_adaptive_entry_research_observer(
    *,
    operational_configuration: object,
    paper_order_book: object | None,
    position_source: object,
    warrior_forward_sidecar: object,
) -> AdaptiveWorkingEntryObserver:
    """Build non-authoritative adaptive-entry research around Warrior state."""

    return AdaptiveWorkingEntryObserver(
        enabled=(
            operational_configuration.adaptive_entry_research_enabled
            and operational_configuration.warrior_forward_paper_enabled
        ),
        environment=operational_configuration.environment.value,
        path=operational_configuration.adaptive_entry_research_path,
        capacity=operational_configuration.adaptive_entry_research_queue_capacity,
        order_source=(
            (lambda _symbol: ())
            if paper_order_book is None
            else paper_order_book.open_orders_for_symbol
        ),
        position_source=position_source,
        warrior_source=warrior_forward_sidecar.adaptive_entry_context,
    )


__all__ = [
    "create_adaptive_entry_research_observer",
    "create_entry_opportunity_value_observer",
]

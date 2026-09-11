"""Explicit local PAPER campaign operations.

Campaign rollover is deliberately separate from runtime startup.  It changes
only campaign metadata; existing orders and events are never rewritten.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .durable_store import DurablePaperExecutionStore


class PaperCampaignService:
    """Application-level entry point for an explicit PAPER rollover."""

    def __init__(self, path: str | Path, *, account_id: str = "paper-account") -> None:
        self._store = DurablePaperExecutionStore(path, account_id=account_id)

    def start_new_campaign(
        self, *, operation_key: str, campaign_id: str | None = None,
        started_at: datetime | None = None, reason: str = "explicit-rollover",
        starting_equity: Decimal | None = None, starting_cash: Decimal | None = None,
        buying_power_multiplier: Decimal = Decimal("1"),
    ) -> str:
        return self._store.start_new_paper_campaign(
            operation_key=operation_key, campaign_id=campaign_id,
            started_at=started_at, reason=reason,
            **({} if starting_equity is None else {"starting_equity": starting_equity}),
            **({} if starting_cash is None else {"starting_cash": starting_cash}),
            buying_power_multiplier=buying_power_multiplier,
        )

    def close(self) -> None:
        self._store.close()


def start_new_paper_campaign(
    path: str | Path, *, operation_key: str, account_id: str = "paper-account",
    campaign_id: str | None = None, started_at: datetime | None = None,
    reason: str = "explicit-rollover", starting_equity: Decimal | None = None,
    starting_cash: Decimal | None = None,
    buying_power_multiplier: Decimal = Decimal("1"),
) -> str:
    """Start a campaign without exposing a raw SQL or file operation."""
    service = PaperCampaignService(path, account_id=account_id)
    try:
        return service.start_new_campaign(
            operation_key=operation_key, campaign_id=campaign_id,
            started_at=started_at, reason=reason,
            starting_equity=starting_equity, starting_cash=starting_cash,
            buying_power_multiplier=buying_power_multiplier,
        )
    finally:
        service.close()


__all__ = ["PaperCampaignService", "start_new_paper_campaign"]

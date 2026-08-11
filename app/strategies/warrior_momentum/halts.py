"""Observation-only halt lifecycle tracking."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .models import HaltObservation


class HaltTracker:
    def __init__(self) -> None:
        self._active: dict[str, tuple[datetime, Decimal]] = {}

    def observe(self, symbol: str, timestamp: datetime, price: Decimal, halted: bool) -> HaltObservation | None:
        normalized = symbol.strip().upper()
        if halted and normalized not in self._active:
            self._active[normalized] = (timestamp, price)
            return HaltObservation(normalized, timestamp)
        if not halted and normalized in self._active:
            entered, prior_price = self._active.pop(normalized)
            return HaltObservation(normalized, entered, timestamp, timestamp - entered,
                                   (price - prior_price) / prior_price * Decimal("100"))
        return None


__all__ = ["HaltTracker"]

from __future__ import annotations

from app.gui.components.metrics import PortfolioCards


class PortfolioMetrics(PortfolioCards):
    @property
    def _cards(self):  # type: ignore[no-untyped-def]
        return self.cards


__all__ = ["PortfolioMetrics"]

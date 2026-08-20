from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.catalysts.models import CatalystEvidence


@runtime_checkable
class CatalystProvider(Protocol):
    @property
    def name(self) -> str:
        """Return a stable source name for attribution."""

    def get_evidence(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> CatalystEvidence:
        """Return normalized catalyst evidence for one symbol."""


__all__ = ["CatalystProvider"]

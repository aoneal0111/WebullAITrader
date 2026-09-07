from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import RLock

from .models import CryptoResearchDecision


class CryptoResearchStatus(StrEnum):
    DISABLED = "DISABLED"
    DISCOVERING = "DISCOVERING"
    ACTIVE = "ACTIVE"
    NO_SUPPORTED_PAIRS = "NO_SUPPORTED_PAIRS"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    AWAITING_DATA = "AWAITING_DATA"


@dataclass(frozen=True, slots=True)
class CryptoResearchViewStatus:
    status: CryptoResearchStatus
    last_failure_category: str | None = None


class CryptoResearchViewStore:
    """Read-only GUI handoff with no route back into research or execution."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._rows: tuple[CryptoResearchDecision, ...] = ()
        self._status = CryptoResearchViewStatus(CryptoResearchStatus.DISABLED)

    def publish(self, rows: tuple[CryptoResearchDecision, ...]) -> None:
        with self._lock:
            self._rows = rows

    def snapshot(self) -> tuple[CryptoResearchDecision, ...]:
        with self._lock:
            return self._rows

    def publish_status(
        self,
        status: CryptoResearchStatus,
        last_failure_category: str | None = None,
    ) -> None:
        if not isinstance(status, CryptoResearchStatus):
            raise TypeError("crypto research status is required")
        with self._lock:
            self._status = CryptoResearchViewStatus(status, last_failure_category)

    def status_snapshot(self) -> CryptoResearchViewStatus:
        with self._lock:
            return self._status


_DEFAULT_VIEW = CryptoResearchViewStore()


def default_crypto_research_view() -> CryptoResearchViewStore:
    return _DEFAULT_VIEW


__all__ = [
    "CryptoResearchStatus",
    "CryptoResearchViewStatus",
    "CryptoResearchViewStore",
    "default_crypto_research_view",
]

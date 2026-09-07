from __future__ import annotations

from threading import RLock

from .models import CryptoResearchDecision


class CryptoResearchViewStore:
    """Read-only GUI handoff with no route back into research or execution."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._rows: tuple[CryptoResearchDecision, ...] = ()

    def publish(self, rows: tuple[CryptoResearchDecision, ...]) -> None:
        with self._lock:
            self._rows = rows

    def snapshot(self) -> tuple[CryptoResearchDecision, ...]:
        with self._lock:
            return self._rows


_DEFAULT_VIEW = CryptoResearchViewStore()


def default_crypto_research_view() -> CryptoResearchViewStore:
    return _DEFAULT_VIEW


__all__ = ["CryptoResearchViewStore", "default_crypto_research_view"]

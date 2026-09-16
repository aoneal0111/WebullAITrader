"""Optional SEC shadow decorator for offline parity wiring."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from .models import CatalystEvidence
from .provider import CatalystProvider
from .sec_shadow_parity import SecCatalystShadowEvaluator


class SecShadowingCatalystProvider:
    """Run shadow diagnostics after one authoritative legacy SEC evaluation."""

    def __init__(
        self,
        legacy_provider: CatalystProvider,
        shadow_evaluator: SecCatalystShadowEvaluator,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(legacy_provider, CatalystProvider):
            raise TypeError("legacy_provider must implement CatalystProvider")
        if not callable(getattr(shadow_evaluator, "evaluate", None)):
            raise TypeError("shadow_evaluator must provide evaluate")
        self._legacy_provider = legacy_provider
        self._shadow_evaluator = shadow_evaluator
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def name(self) -> str:
        return self._legacy_provider.name

    def get_evidence(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> CatalystEvidence:
        effective_as_of = as_of if as_of is not None else self._clock()
        legacy_evidence = self._legacy_provider.get_evidence(
            symbol, as_of=effective_as_of,
        )
        try:
            self._shadow_evaluator.evaluate(
                symbol,
                as_of=effective_as_of,
                legacy_evidence=legacy_evidence,
            )
        except Exception:
            # Shadow diagnostics are strictly non-authoritative. Preserve the
            # legacy provider's result if an unexpected evaluator failure occurs.
            pass
        return legacy_evidence


__all__ = ["SecShadowingCatalystProvider"]

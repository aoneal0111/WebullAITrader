"""Desktop-owned construction of the optional PAPER SEC shadow runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Callable

from app.catalysts.sec_shadow_parity import SecCatalystParityStore, SecCatalystShadowEvaluator
from app.catalysts.sec_symbol_intelligence_adapter import SecSymbolIntelligenceCatalystAdapter
from app.configuration.models import OperationalConfiguration, TradingEnvironment
from app.symbol_intelligence.composition import (
    SymbolIntelligenceRepositoryComposition,
    create_symbol_intelligence_repository_composition,
)


class SecShadowRuntimeState(StrEnum):
    DISABLED = "DISABLED"
    INELIGIBLE_ENVIRONMENT = "INELIGIBLE_ENVIRONMENT"
    NO_LEGACY_PROVIDER = "NO_LEGACY_PROVIDER"
    NO_SYMBOL_INTELLIGENCE = "NO_SYMBOL_INTELLIGENCE"
    CONSTRUCTION_ERROR = "CONSTRUCTION_ERROR"
    ACTIVE = "ACTIVE"


@dataclass(slots=True)
class SecShadowRuntimeComposition:
    state: SecShadowRuntimeState
    repository_composition: SymbolIntelligenceRepositoryComposition | None = None
    adapter: object | None = None
    store: SecCatalystParityStore | None = None
    evaluator: SecCatalystShadowEvaluator | None = None


def create_sec_shadow_runtime(
    configuration: OperationalConfiguration,
    *,
    clock: Callable[[], datetime] | None = None,
    repository_composition_factory: Callable[..., SymbolIntelligenceRepositoryComposition | None] = create_symbol_intelligence_repository_composition,
    adapter_factory: Callable[..., object] = SecSymbolIntelligenceCatalystAdapter,
    store_factory: Callable[..., SecCatalystParityStore] = SecCatalystParityStore,
    evaluator_factory: Callable[..., SecCatalystShadowEvaluator] = SecCatalystShadowEvaluator,
) -> SecShadowRuntimeComposition:
    """Build shadow dependencies only after all eligibility gates pass."""

    sec_config = configuration.symbol_intelligence_sec_edgar
    if not sec_config.shadow_parity_enabled:
        return SecShadowRuntimeComposition(SecShadowRuntimeState.DISABLED)
    if configuration.environment is not TradingEnvironment.PAPER:
        return SecShadowRuntimeComposition(SecShadowRuntimeState.INELIGIBLE_ENVIRONMENT)
    if configuration.sec_edgar is None:
        return SecShadowRuntimeComposition(SecShadowRuntimeState.NO_LEGACY_PROVIDER)
    try:
        repository_composition = repository_composition_factory(
            configuration, environment=configuration.environment.value,
        )
    except Exception:
        return SecShadowRuntimeComposition(SecShadowRuntimeState.NO_SYMBOL_INTELLIGENCE)
    if repository_composition is None:
        return SecShadowRuntimeComposition(SecShadowRuntimeState.NO_SYMBOL_INTELLIGENCE)

    effective_clock = clock or (lambda: datetime.now(UTC))
    try:
        adapter = adapter_factory(
            repository_composition.repository,
            freshness_days=sec_config.freshness_days,
            clock=effective_clock,
        )
        store = store_factory(
            SecCatalystParityStore.production_path(configuration.environment.value),
        )
        evaluator = evaluator_factory(
            adapter=adapter,
            store=store,
            environment=configuration.environment.value,
            clock=effective_clock,
        )
    except Exception:
        return SecShadowRuntimeComposition(
            SecShadowRuntimeState.CONSTRUCTION_ERROR,
            repository_composition=repository_composition,
        )
    return SecShadowRuntimeComposition(
        SecShadowRuntimeState.ACTIVE,
        repository_composition=repository_composition,
        adapter=adapter,
        store=store,
        evaluator=evaluator,
    )


__all__ = [
    "SecShadowRuntimeComposition",
    "SecShadowRuntimeState",
    "create_sec_shadow_runtime",
]

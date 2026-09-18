from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Callable

from app.composition.sec_shadow_runtime import (
    SecShadowRuntimeComposition,
    SecShadowRuntimeState,
)
from app.symbol_intelligence.composition import (
    SymbolIntelligenceComposition,
    SymbolIntelligenceRepositoryComposition,
    create_symbol_intelligence_composition,
)
from app.symbol_intelligence.network_ownership_runtime import (
    evaluate_sec_acquisition_eligibility,
)


@dataclass(slots=True)
class DesktopSymbolIntelligenceBundle:
    symbol_intelligence: SymbolIntelligenceComposition | None
    sec_shadow_runtime: SecShadowRuntimeComposition
    activation_state: str
    shadow_construction_failed: bool = False


def create_desktop_symbol_intelligence_bundle(
    *,
    operational_configuration: object,
    shadow_runtime_factory: Callable[..., SecShadowRuntimeComposition],
) -> DesktopSymbolIntelligenceBundle:
    """Construct optional SEC/symbol-intelligence services outside trading core."""

    symbol_intelligence = None
    activation_state = "DISABLED"
    activation_state_holder = [activation_state]
    try:
        eligible = evaluate_sec_acquisition_eligibility(
            operational_configuration
        ).eligible
        if not eligible:
            activation_state = "INELIGIBLE"
        if eligible:
            symbol_intelligence = create_symbol_intelligence_composition(
                operational_configuration,
                activate=True,
                start=False,
                activation_diagnostics_callback=(
                    lambda state: activation_state_holder.__setitem__(0, state)
                ),
            )
            activation_state = activation_state_holder[0]
            activation_state = (
                "ACTIVE"
                if symbol_intelligence is not None
                else "LEASE_DENIED"
            )
    except Exception:
        symbol_intelligence = None
        activation_state = "CONSTRUCTION_ERROR"

    shared_repository_composition = (
        None
        if symbol_intelligence is None
        else SymbolIntelligenceRepositoryComposition(
            symbol_intelligence.repository
        )
    )

    shadow_construction_failed = False
    try:
        if shared_repository_composition is not None:
            parameters = inspect.signature(shadow_runtime_factory).parameters
            accepts_repository = (
                "repository_composition" in parameters
                or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters.values()
                )
            )
            if accepts_repository:
                sec_shadow_runtime = shadow_runtime_factory(
                    operational_configuration,
                    repository_composition=shared_repository_composition,
                )
            else:
                sec_shadow_runtime = shadow_runtime_factory(
                    operational_configuration
                )
        else:
            sec_shadow_runtime = shadow_runtime_factory(
                operational_configuration
            )
    except Exception:
        shadow_construction_failed = True
        activation_state = "CONSTRUCTION_ERROR"
        sec_shadow_runtime = SecShadowRuntimeComposition(
            state=SecShadowRuntimeState.CONSTRUCTION_ERROR,
        )
        if symbol_intelligence is not None:
            if symbol_intelligence.close(timeout_seconds=5.0):
                symbol_intelligence = None

    return DesktopSymbolIntelligenceBundle(
        symbol_intelligence=symbol_intelligence,
        sec_shadow_runtime=sec_shadow_runtime,
        activation_state=activation_state,
        shadow_construction_failed=shadow_construction_failed,
    )


def start_desktop_symbol_intelligence(
    bundle: DesktopSymbolIntelligenceBundle,
) -> str:
    """Start optional symbol intelligence while preserving failure isolation."""

    symbol_intelligence = bundle.symbol_intelligence
    if symbol_intelligence is None or bundle.shadow_construction_failed:
        return bundle.activation_state

    activation_state = bundle.activation_state
    try:
        if not symbol_intelligence.start():
            if symbol_intelligence.close():
                bundle.symbol_intelligence = None
            activation_state = "START_ERROR"
    except Exception:
        try:
            cleanup_complete = symbol_intelligence.close()
        except Exception:
            cleanup_complete = False
        if cleanup_complete:
            bundle.symbol_intelligence = None
        activation_state = "START_ERROR"

    bundle.activation_state = (
        "ACTIVE"
        if bundle.symbol_intelligence is not None
        else activation_state
    )
    return bundle.activation_state


__all__ = [
    "DesktopSymbolIntelligenceBundle",
    "create_desktop_symbol_intelligence_bundle",
    "start_desktop_symbol_intelligence",
]

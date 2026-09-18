from __future__ import annotations

import os
from dataclasses import dataclass
from types import SimpleNamespace

from app.strategies.warrior_momentum.configuration import WarriorMomentumConfig
from app.strategies.warrior_momentum.observability import (
    create_warrior_observability_sink,
)
from app.trade_intelligence.runtime import TradeIntelligenceRuntimeObserver


@dataclass(slots=True)
class DesktopIntelligenceComposition:
    warrior_strategy_config: WarriorMomentumConfig
    warrior_observability: object
    trade_intelligence_observer: TradeIntelligenceRuntimeObserver


def create_desktop_intelligence_composition(
    *,
    operational_configuration: object,
) -> DesktopIntelligenceComposition:
    """Compose advisory intelligence and Warrior observability outside the core root."""

    warrior_observability = create_warrior_observability_sink(
        SimpleNamespace(
            observability_enabled=(
                os.getenv(
                    "ATLAS_WARRIOR_D3_DIAGNOSTICS_ENABLED",
                    "false",
                ).strip().lower()
                == "true"
            ),
            observability_root=(
                os.getenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_ROOT") or None
            ),
            observability_session_id=(
                os.getenv("ATLAS_WARRIOR_D3_DIAGNOSTICS_SESSION_ID") or None
            ),
        )
    )

    configured_warrior_strategy = WarriorMomentumConfig.from_env()
    warrior_strategy_config = WarriorMomentumConfig(
        adaptive_context_enabled=(
            configured_warrior_strategy.adaptive_context_enabled
            and operational_configuration.environment.value == "PAPER"
        )
    )

    trade_intelligence_observer = TradeIntelligenceRuntimeObserver(
        enabled=(
            operational_configuration.trade_intelligence_enabled
            and not operational_configuration.live_trading_enabled
        ),
        environment=operational_configuration.environment.value,
        path=operational_configuration.trade_intelligence_path,
        capacity=operational_configuration.trade_intelligence_queue_capacity,
        observability=warrior_observability,
        warrior_observation_enabled=(
            warrior_strategy_config.adaptive_context_enabled
        ),
    )

    return DesktopIntelligenceComposition(
        warrior_strategy_config=warrior_strategy_config,
        warrior_observability=warrior_observability,
        trade_intelligence_observer=trade_intelligence_observer,
    )


__all__ = [
    "DesktopIntelligenceComposition",
    "create_desktop_intelligence_composition",
]

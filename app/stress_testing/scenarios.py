from __future__ import annotations

from app.stress_testing.models import ScenarioFilter, ScenarioKind


def effective_filter(kind: ScenarioKind, supplied: tuple[ScenarioFilter, ...]) -> ScenarioFilter:
    matches = tuple(item for item in supplied if item.scenario is kind)
    if len(matches) > 1:
        raise ValueError(f"multiple filters supplied for {kind.value}")
    base = matches[0] if matches else ScenarioFilter(kind)
    defaults = {
        ScenarioKind.BEAR_MARKET: ("trend_regime", "BEAR"),
        ScenarioKind.HIGH_VOLATILITY: ("volatility_regime", "HIGH"),
        ScenarioKind.LOW_VOLATILITY: ("volatility_regime", "LOW"),
        ScenarioKind.TRENDING_MARKET: ("trend_regime", "TRENDING"),
        ScenarioKind.SIDEWAYS_MARKET: ("trend_regime", "SIDEWAYS"),
    }
    if kind in defaults:
        field, value = defaults[kind]
        if getattr(base, field) is None:
            return ScenarioFilter(**{**{name: getattr(base, name) for name in base.__dataclass_fields__}, field: value})
    return base


def prerequisite(filter_: ScenarioFilter) -> str | None:
    if filter_.minimum_gap_percent is not None:
        return "authoritative gap observations are not recorded"
    if filter_.maximum_volume is not None:
        return "volume cannot be treated as authoritative liquidity"
    if filter_.minimum_spread is not None:
        return "authoritative spread observations are not recorded"
    required = {
        ScenarioKind.MARKET_CRASH: (filter_.minimum_drawdown, "an explicit minimum_drawdown is required"),
        ScenarioKind.GAP_HEAVY: (None, "authoritative gap observations are not recorded"),
        ScenarioKind.LOW_LIQUIDITY: (None, "authoritative liquidity observations are not recorded"),
        ScenarioKind.HIGH_SPREAD: (None, "authoritative spread observations are not recorded"),
        ScenarioKind.HIGH_SLIPPAGE: (filter_.minimum_absolute_slippage, "an explicit minimum_absolute_slippage is required"),
    }
    item = required.get(filter_.scenario)
    return item[1] if item is not None and item[0] is None else None

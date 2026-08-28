"""Typed configuration for the observational Warrior momentum experiment."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
import os

from app.live_scanner.session import ScannerSession


WARRIOR_ENTRY_ALLOWED_SESSIONS = frozenset({
    ScannerSession.PREMARKET.value,
    ScannerSession.REGULAR.value,
    ScannerSession.AFTER_HOURS.value,
})
BALANCED_POLICY_VERSION = "BALANCED_V1"
CONSERVATIVE_POLICY_VERSION = "CONSERVATIVE_V1"


class AtlasStrategy(StrEnum):
    EXISTING = "existing"
    WARRIOR_MOMENTUM_V1 = "warrior_momentum_v1"


@dataclass(frozen=True, slots=True)
class StrategySelection:
    selected: AtlasStrategy = AtlasStrategy.EXISTING
    warrior_live_execution_enabled: bool = False

    @classmethod
    def from_env(cls) -> "StrategySelection":
        raw = os.getenv("ATLAS_STRATEGY", AtlasStrategy.EXISTING.value).strip().lower()
        try:
            selected = AtlasStrategy(raw)
        except ValueError as exc:
            raise ValueError(f"unsupported ATLAS_STRATEGY: {raw}") from exc
        live = os.getenv("WARRIOR_MOMENTUM_V1_LIVE_EXECUTION_ENABLED", "false").strip().lower()
        if live not in {"true", "false"}:
            raise ValueError("WARRIOR_MOMENTUM_V1_LIVE_EXECUTION_ENABLED must be true or false")
        # V1 is deliberately incapable of live authorization, even if a hostile
        # environment attempts to set the future-facing flag.
        return cls(selected=selected, warrior_live_execution_enabled=False)


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    percentage_change: Decimal = Decimal("20")
    relative_volume: Decimal = Decimal("20")
    short_term_acceleration: Decimal = Decimal("15")
    float_quality: Decimal = Decimal("15")
    liquidity: Decimal = Decimal("10")
    catalyst_quality: Decimal = Decimal("10")
    technical_setup_quality: Decimal = Decimal("5")
    execution_quality: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.values()) or sum(self.values()) != Decimal("100"):
            raise ValueError("score weights must be non-negative and total 100")

    def values(self) -> tuple[Decimal, ...]:
        return tuple(getattr(self, name) for name in self.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    minimum_price: Decimal = Decimal("1.00")
    maximum_price: Decimal = Decimal("30.00")
    minimum_percentage_change: Decimal = Decimal("5")
    minimum_relative_volume: Decimal = Decimal("2")
    maximum_float: Decimal = Decimal("50000000")
    minimum_volume: Decimal = Decimal("0")
    minimum_dollar_volume: Decimal = Decimal("1000000")
    maximum_spread_percent: Decimal = Decimal("1.50")
    watch_score: Decimal = Decimal("25")
    near_qualified_score: Decimal = Decimal("45")
    qualified_score: Decimal = Decimal("60")


@dataclass(frozen=True, slots=True)
class SetupConfig:
    hod_proximity_percent: Decimal = Decimal("1")
    breakout_buffer_percent: Decimal = Decimal("0.05")
    minimum_breakout_volume_ratio: Decimal = Decimal("1.20")
    minimum_impulse_percent: Decimal = Decimal("4")
    minimum_pullback_bars: int = 2
    maximum_pullback_bars: int = 6
    maximum_micro_pullback_percent: Decimal = Decimal("3")
    bull_flag_minimum_retracement: Decimal = Decimal("0.10")
    bull_flag_maximum_retracement: Decimal = Decimal("0.50")
    flat_top_tests: int = 3
    flat_top_tolerance_percent: Decimal = Decimal("0.35")
    minimum_consolidation_bars: int = 2
    recent_swing_lookback: int = 5


@dataclass(frozen=True, slots=True)
class EntryConfig:
    minimum_momentum_score: Decimal = Decimal("55")
    minimum_setup_score: Decimal = Decimal("55")
    maximum_spread_percent: Decimal = Decimal("1.25")
    minimum_dollar_volume: Decimal = Decimal("2500000")
    require_catalyst_for_entry: bool = False
    maximum_risk_per_share: Decimal = Decimal("1.00")
    allowed_sessions: frozenset[str] = WARRIOR_ENTRY_ALLOWED_SESSIONS


@dataclass(frozen=True, slots=True)
class RiskConfig:
    configured_per_trade_risk: Decimal = Decimal("100")
    equity_risk_percentage: Decimal = Decimal("0.005")
    maximum_quantity: int = 10000
    maximum_position_dollars: Decimal = Decimal("25000")


@dataclass(frozen=True, slots=True)
class TradeManagementConfig:
    first_target_r: Decimal = Decimal("1")
    first_target_exit_percent: Decimal = Decimal("0.50")
    second_target_r: Decimal = Decimal("2")
    second_target_exit_percent: Decimal = Decimal("0.25")
    runner_percent: Decimal = Decimal("0.25")
    move_stop_to_breakeven_after_r: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.first_target_exit_percent + self.second_target_exit_percent + self.runner_percent != 1:
            raise ValueError("trade exit percentages must total 1")


@dataclass(frozen=True, slots=True)
class WarriorMomentumConfig:
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    setups: SetupConfig = field(default_factory=SetupConfig)
    entry: EntryConfig = field(default_factory=EntryConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    trade_management: TradeManagementConfig = field(default_factory=TradeManagementConfig)
    top_gapper_count: int = 10
    telemetry_symbol_limit: int = 10
    live_execution_enabled: bool = False
    policy_version: str = BALANCED_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.live_execution_enabled:
            raise ValueError("WARRIOR_MOMENTUM_V1 is paper/replay only")

    @classmethod
    def conservative_v1(cls) -> "WarriorMomentumConfig":
        """Return the exact pre-Balanced policy for research comparison."""
        return cls(
            discovery=DiscoveryConfig(
                maximum_price=Decimal("20.00"),
                minimum_percentage_change=Decimal("10"),
                minimum_relative_volume=Decimal("5"),
                maximum_float=Decimal("20000000"),
                minimum_volume=Decimal("100000"),
                maximum_spread_percent=Decimal("1"),
            ),
            entry=EntryConfig(
                minimum_momentum_score=Decimal("60"),
                minimum_setup_score=Decimal("60"),
                maximum_spread_percent=Decimal("1"),
                minimum_dollar_volume=Decimal("5000000"),
                require_catalyst_for_entry=True,
            ),
            policy_version=CONSERVATIVE_POLICY_VERSION,
        )


__all__ = [
    "AtlasStrategy", "StrategySelection", "ScoreWeights", "DiscoveryConfig",
    "SetupConfig", "EntryConfig", "RiskConfig", "TradeManagementConfig",
    "WarriorMomentumConfig", "WARRIOR_ENTRY_ALLOWED_SESSIONS",
    "BALANCED_POLICY_VERSION", "CONSERVATIVE_POLICY_VERSION",
]

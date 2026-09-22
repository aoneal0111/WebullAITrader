"""Typed configuration for the observational Warrior momentum experiment."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
import os
from pathlib import Path
import re

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
    maximum_price: Decimal = Decimal("100.00")
    minimum_percentage_change: Decimal = Decimal("5")
    minimum_relative_volume: Decimal = Decimal("2")
    maximum_float: Decimal = Decimal("50000000")
    minimum_volume: Decimal = Decimal("0")
    minimum_dollar_volume: Decimal = Decimal("250000")
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
class AdaptiveEntryConfig:
    """Bounded, observation-driven entry replacement policy."""

    enabled: bool = True
    max_replacements: int = 2
    min_reprice_interval_seconds: Decimal = Decimal("5.0")
    max_lifecycles_per_opportunity: int = 3
    max_displacement_percent: Decimal = Decimal("1.5")
    max_displacement_absolute: Decimal = Decimal("0.05")

    def __post_init__(self) -> None:
        if self.max_replacements < 0:
            raise ValueError("adaptive entry replacement count cannot be negative")
        if self.max_lifecycles_per_opportunity <= 0:
            raise ValueError("adaptive entry lifecycle count must be positive")
        if self.min_reprice_interval_seconds < 0:
            raise ValueError("adaptive entry reprice interval cannot be negative")
        if (
            self.max_displacement_percent <= 0
            or self.max_displacement_absolute <= 0
        ):
            raise ValueError("adaptive entry displacement caps must be positive")


@dataclass(frozen=True, slots=True)
class RiskConfig:
    configured_per_trade_risk: Decimal = Decimal("100")
    equity_risk_percentage: Decimal = Decimal("0.005")
    maximum_quantity: int = 10000
    maximum_position_dollars: Decimal = Decimal("25000")
    maximum_position_equity_percentage: Decimal = Decimal("0.50")

    maximum_stop_distance_percent: Decimal = Decimal("8")
    maximum_campaign_loss_fraction: Decimal = Decimal("0.02")
    maximum_gross_exposure_fraction: Decimal = Decimal("0.50")

    def __post_init__(self) -> None:
        for name in ("configured_per_trade_risk", "equity_risk_percentage",
                     "maximum_position_dollars", "maximum_position_equity_percentage",
                     "maximum_stop_distance_percent", "maximum_campaign_loss_fraction",
                     "maximum_gross_exposure_fraction"):
            value = getattr(self, name)
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("equity_risk_percentage", "maximum_position_equity_percentage",
                     "maximum_campaign_loss_fraction", "maximum_gross_exposure_fraction"):
            if getattr(self, name) > 1:
                raise ValueError(f"{name} must not exceed one")
        if self.maximum_stop_distance_percent > 100:
            raise ValueError("maximum_stop_distance_percent must not exceed 100")
        if self.maximum_quantity <= 0:
            raise ValueError("maximum_quantity must be positive")


@dataclass(frozen=True, slots=True)
class TradeManagementConfig:
    first_target_r: Decimal = Decimal("1")
    first_target_exit_percent: Decimal = Decimal("0.50")
    second_target_r: Decimal = Decimal("2")
    second_target_exit_percent: Decimal = Decimal("0.25")
    runner_percent: Decimal = Decimal("0.25")
    move_stop_to_breakeven_after_r: Decimal = Decimal("1")
    profit_defense_enabled: bool = True
    profit_defense_activation_r: Decimal = Decimal("1.50")
    profit_defense_tighten_giveback_r: Decimal = Decimal("0.45")
    profit_defense_exit_activation_r: Decimal = Decimal("2.50")
    profit_defense_exit_giveback_r: Decimal = Decimal("0.75")
    adaptive_exit_enabled: bool = True
    initial_stop_volatility_multiplier: Decimal = Decimal("1.50")
    initial_stop_spread_multiplier: Decimal = Decimal("2.50")
    initial_stop_max_widening_r: Decimal = Decimal("2.00")
    exit_range_lookback: int = 5
    exit_volatility_baseline_r: Decimal = Decimal("0.50")
    exit_volatility_allowance_max_r: Decimal = Decimal("0.35")
    exit_supportive_allowance_r: Decimal = Decimal("0.20")
    exit_adverse_reduction_r: Decimal = Decimal("0.10")
    exit_depth_imbalance_threshold: Decimal = Decimal("0.15")
    exit_minimum_giveback_r: Decimal = Decimal("0.25")
    max_add_on_legs: int = 1

    def __post_init__(self) -> None:
        if self.first_target_exit_percent + self.second_target_exit_percent + self.runner_percent != 1:
            raise ValueError("trade exit percentages must total 1")
        for value in (
            self.profit_defense_activation_r,
            self.profit_defense_tighten_giveback_r,
            self.profit_defense_exit_activation_r,
            self.profit_defense_exit_giveback_r,
            self.exit_volatility_baseline_r,
            self.exit_volatility_allowance_max_r,
            self.exit_supportive_allowance_r,
            self.exit_adverse_reduction_r,
            self.exit_depth_imbalance_threshold,
            self.exit_minimum_giveback_r,
        ):
            if value < 0:
                raise ValueError("profit-defense thresholds must be non-negative")
        if (
            self.initial_stop_volatility_multiplier <= 0
            or self.initial_stop_spread_multiplier <= 0
            or self.initial_stop_max_widening_r < 1
            or self.exit_range_lookback <= 0
        ):
            raise ValueError("adaptive exit bounds must be positive")
        if self.max_add_on_legs != 1:
            raise ValueError("Phase 1 supports exactly one add-on leg")


@dataclass(frozen=True, slots=True)
class SessionManagementConfig:
    """Bounded PAPER policy for the after-hours/overnight boundary."""

    enabled: bool = True
    after_hours_entry_cutoff_minutes: int = 30
    flatten_lead_minutes: int = 5
    overnight_carry_enabled: bool = False
    overnight_minimum_current_r: Decimal = Decimal("0.50")
    overnight_minimum_peak_r: Decimal = Decimal("1.00")
    overnight_maximum_giveback_r: Decimal = Decimal("0.75")
    overnight_minimum_pressure_score: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.flatten_lead_minutes <= self.after_hours_entry_cutoff_minutes < 240:
            raise ValueError("session-management minute bounds are invalid")
        if any(value < 0 for value in (
            self.overnight_minimum_current_r,
            self.overnight_minimum_peak_r,
            self.overnight_maximum_giveback_r,
        )):
            raise ValueError("overnight risk thresholds must be non-negative")


@dataclass(frozen=True, slots=True)
class WarriorMomentumConfig:
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    setups: SetupConfig = field(default_factory=SetupConfig)
    entry: EntryConfig = field(default_factory=EntryConfig)
    adaptive_entry: AdaptiveEntryConfig = field(default_factory=AdaptiveEntryConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    trade_management: TradeManagementConfig = field(default_factory=TradeManagementConfig)
    session_management: SessionManagementConfig = field(default_factory=SessionManagementConfig)
    top_gapper_count: int = 10
    telemetry_symbol_limit: int = 10
    live_execution_enabled: bool = False
    policy_version: str = BALANCED_POLICY_VERSION
    observability_enabled: bool = False
    observability_root: Path | None = field(default=None, repr=False)
    observability_session_id: str | None = field(default=None, repr=False)
    adaptive_context_enabled: bool = False

    def __post_init__(self) -> None:
        if self.live_execution_enabled:
            raise ValueError("WARRIOR_MOMENTUM_V1 is paper/replay only")
        if self.observability_enabled:
            if self.observability_root is None:
                raise ValueError("observability root is required when enabled")
            session_id = str(self.observability_session_id or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", session_id):
                raise ValueError("observability session ID is invalid")
            object.__setattr__(self, "observability_session_id", session_id)
        if self.observability_root is not None:
            object.__setattr__(self, "observability_root", Path(self.observability_root))

    @classmethod
    def from_env(cls) -> "WarriorMomentumConfig":
        """Build the normal config, enabling adaptive context only explicitly."""
        # Adaptive participation is the PAPER default.  It replaces the fixed
        # 30-day RVOL gate with bounded intraday evidence while the desktop
        # composition still forces it off outside PAPER.  Operators retain an
        # explicit false kill switch.
        raw = os.getenv("ATLAS_WARRIOR_ADAPTIVE_CONTEXT_ENABLED", "true").strip().lower()
        if raw not in {"true", "false"}:
            raise ValueError("ATLAS_WARRIOR_ADAPTIVE_CONTEXT_ENABLED must be true or false")
        diagnostics = os.getenv(
            "ATLAS_WARRIOR_D3_DIAGNOSTICS_ENABLED", "false",
        ).strip().lower()
        if diagnostics not in {"true", "false"}:
            raise ValueError(
                "ATLAS_WARRIOR_D3_DIAGNOSTICS_ENABLED must be true or false"
            )
        diagnostics_root = os.getenv(
            "ATLAS_WARRIOR_D3_DIAGNOSTICS_ROOT", "",
        ).strip()
        diagnostics_session_id = os.getenv(
            "ATLAS_WARRIOR_D3_DIAGNOSTICS_SESSION_ID", "",
        ).strip()
        overnight = os.getenv(
            "ATLAS_WARRIOR_OVERNIGHT_CARRY_ENABLED", "false",
        ).strip().lower()
        if overnight not in {"true", "false"}:
            raise ValueError("ATLAS_WARRIOR_OVERNIGHT_CARRY_ENABLED must be true or false")
        return cls(
            adaptive_context_enabled=raw == "true",
            observability_enabled=diagnostics == "true",
            observability_root=(
                Path(diagnostics_root) if diagnostics_root else None
            ),
            observability_session_id=diagnostics_session_id or None,
            session_management=SessionManagementConfig(
                overnight_carry_enabled=overnight == "true",
            ),
        )

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
    "SetupConfig", "EntryConfig", "AdaptiveEntryConfig", "RiskConfig", "TradeManagementConfig",
    "SessionManagementConfig",
    "WarriorMomentumConfig", "WARRIOR_ENTRY_ALLOWED_SESSIONS",
    "BALANCED_POLICY_VERSION", "CONSERVATIVE_POLICY_VERSION",
]

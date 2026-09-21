from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.strategies.warrior_momentum.adaptive_exit import (
    AdaptiveExitEvidence,
    adapt_initial_stop,
    assess_adaptive_exit,
)
from app.strategies.warrior_momentum.configuration import TradeManagementConfig
from app.strategies.warrior_momentum.models import (
    MomentumEntrySignal,
    SetupType,
    StopModel,
)
from app.momentum_scanner.models import CatalystStatus
from app.strategies.warrior_momentum.models import MinuteBar


D = Decimal
NOW = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)


def signal() -> MomentumEntrySignal:
    return MomentumEntrySignal(
        strategy_id="WARRIOR_MOMENTUM_V1", symbol="XYZ", timestamp=NOW,
        session="REGULAR", momentum_score=D("80"), setup_type=SetupType.BULL_FLAG,
        entry_trigger=D("2.00"), reference_price=D("2.00"),
        stop_price=D("1.97"), stop_model=StopModel.PATTERN_LOW,
        risk_per_share=D("0.03"),
        target_levels=(D("2.03"), D("2.06"), D("2.09")),
        catalyst_state=CatalystStatus.TRUE, relative_volume=D("20"),
        float_shares=D("5000000"), spread_percent=D("1"),
        volume=D("1000000"), dollar_volume=D("2000000"),
        setup_score=D("80"), reasoning_codes=(),
    )


def bars() -> tuple[MinuteBar, ...]:
    return tuple(
        MinuteBar(
            "XYZ", NOW - timedelta(minutes=5 - index),
            D("1.90"), D("1.96"), D("1.88"), D("1.94"), D("10000"),
        )
        for index in range(5)
    )


def test_initial_stop_adds_pre_entry_room_and_rebuilds_r_targets() -> None:
    original = signal()
    adapted = adapt_initial_stop(
        original, bars(), spread_percent=D("1"),
        config=TradeManagementConfig(), maximum_risk_per_share=D("1"),
    )

    assert adapted.structural_stop_price == original.stop_price
    assert adapted.stop_price == D("1.94")
    assert adapted.risk_per_share == D("0.06")
    assert adapted.target_levels == (D("2.06"), D("2.12"), D("2.18"))


def test_initial_stop_never_widens_beyond_configured_cap() -> None:
    config = replace(
        TradeManagementConfig(), initial_stop_max_widening_r=D("1.5"),
    )
    adapted = adapt_initial_stop(
        signal(), bars(), spread_percent=D("10"),
        config=config, maximum_risk_per_share=D("1"),
    )
    assert adapted.risk_per_share == D("0.045")
    assert adapted.stop_price == D("1.955")


def test_supportive_flow_and_depth_give_soft_exit_more_room() -> None:
    config = TradeManagementConfig()
    neutral = assess_adaptive_exit(
        base_tighten_giveback_r=config.profit_defense_tighten_giveback_r,
        base_runner_exit_giveback_r=config.profit_defense_exit_giveback_r,
        recent_ranges=(D("0.02"),), risk_per_share=D("0.10"),
        evidence=None, config=config,
    )
    supportive = assess_adaptive_exit(
        base_tighten_giveback_r=config.profit_defense_tighten_giveback_r,
        base_runner_exit_giveback_r=config.profit_defense_exit_giveback_r,
        recent_ranges=(D("0.02"),), risk_per_share=D("0.10"),
        evidence=AdaptiveExitEvidence(
            NOW, True, D("2.00"), D("2.02"), D("0.40"), D("2.015"),
            "STRONG_ACCUMULATION", True,
        ),
        config=config,
    )
    assert supportive.pressure_score >= 2
    assert supportive.tighten_giveback_r > neutral.tighten_giveback_r
    assert supportive.runner_exit_giveback_r > neutral.runner_exit_giveback_r


def test_stale_microstructure_is_neutral_and_cannot_loosen_hard_risk() -> None:
    config = TradeManagementConfig()
    stale = assess_adaptive_exit(
        base_tighten_giveback_r=config.profit_defense_tighten_giveback_r,
        base_runner_exit_giveback_r=config.profit_defense_exit_giveback_r,
        recent_ranges=(), risk_per_share=D("0.10"),
        evidence=AdaptiveExitEvidence(
            NOW, False, D("2"), D("2.10"), D("0.9"), D("2.09"),
            "STRONG_ACCUMULATION", True,
        ),
        config=config,
    )
    assert stale.pressure_score == 0
    assert stale.tighten_giveback_r == config.profit_defense_tighten_giveback_r
    assert "MICROSTRUCTURE_NEUTRAL" in stale.reasons

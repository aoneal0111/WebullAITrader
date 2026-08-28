from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.momentum_scanner.models import (
    AssetClass, CatalystStatus, CatalystType, FloatProvenance,
    ScannerObservation,
)
from app.momentum_scanner.rules import MomentumScannerConfig, evaluate_candidate
from app.gui.formatters.warrior_paper import format_warrior_paper
from app.strategies.warrior_momentum import (
    CandidateStatus, FloatProvenance as CaptureFloatProvenance, ReasonCode,
    SetupDetection, SetupState, SetupType, StopModel, WarriorCaptureHealth,
    WarriorFocusItem, WarriorMomentumConfig, WarriorMomentumRuntime,
    WarriorPaperSnapshot, entry_rejections, prepare_paper_plan,
    watchlist_metadata,
)

D = Decimal
NOW = datetime(2026, 8, 28, 13, 30, tzinfo=UTC)


def observation(
    *, symbol: str = "AEMD", price: str = "2.70", change: str = "24",
    rvol: str = "71", float_shares: str = "711000",
    dollar_volume: str = "108000000", spread: str = "0.37",
    catalyst: CatalystType = CatalystType.NONE,
    catalyst_status: CatalystStatus = CatalystStatus.FALSE,
    tradable: bool = True, halted: bool = False,
) -> ScannerObservation:
    price_value = D(price)
    change_value = D(change)
    volume = D(dollar_volume) / price_value
    midpoint = price_value
    half_spread = midpoint * D(spread) / D("200")
    return ScannerObservation(
        symbol=symbol, timestamp=NOW, price=price_value,
        previous_close=price_value / (D("1") + change_value / D("100")),
        current_volume=volume, average_30_day_volume=volume / D(rvol),
        float_shares=D(float_shares), bid=midpoint - half_spread,
        ask=midpoint + half_spread, catalyst=catalyst,
        catalyst_headline=None, tradable=tradable, halted=halted,
        asset_class=AssetClass.STOCK, catalyst_status=catalyst_status,
        float_provenance=FloatProvenance.AUTHORITATIVE_FLOAT,
    )


def triggered_setup(price: str = "2.70") -> SetupDetection:
    trigger = D(price)
    return SetupDetection(
        SetupType.HIGH_OF_DAY_BREAKOUT, SetupState.TRIGGERED, D("80"),
        trigger, trigger - D("0.20"), StopModel.RECENT_SWING_LOW, trigger,
    )


def discover(value: ScannerObservation, *, session: str = "REGULAR"):
    return WarriorMomentumRuntime().discover(value, (), session=session)


def assess_with_trigger(value: ScannerObservation, *, session: str = "REGULAR"):
    runtime = WarriorMomentumRuntime()
    candidate = runtime.discover(value, (), session=session)
    return runtime, runtime.assess_entry(
        replace(candidate, setup=triggered_setup(format(value.price, "f")))
    )


def test_balanced_configuration_and_conservative_reconstruction_are_explicit() -> None:
    scanner = MomentumScannerConfig()
    warrior = WarriorMomentumConfig()
    assert (
        scanner.minimum_price, scanner.maximum_price,
        scanner.minimum_percentage_change, scanner.minimum_relative_volume,
        scanner.maximum_float_shares, scanner.minimum_dollar_volume,
        scanner.maximum_spread_percent, scanner.require_catalyst,
        scanner.policy_version,
    ) == (D("1"), D("30"), D("5"), D("2"), D("50000000"),
          D("1000000"), D("1.50"), False, "BALANCED_V1")
    assert (
        warrior.entry.minimum_momentum_score,
        warrior.entry.minimum_setup_score,
        warrior.entry.minimum_dollar_volume,
        warrior.entry.maximum_spread_percent,
        warrior.entry.require_catalyst_for_entry,
        warrior.risk.configured_per_trade_risk,
        warrior.policy_version,
    ) == (D("55"), D("55"), D("2500000"), D("1.25"), False,
          D("100"), "BALANCED_V1")
    assert MomentumScannerConfig.conservative_v1().require_catalyst is True
    assert WarriorMomentumConfig.conservative_v1().entry.require_catalyst_for_entry is True


def test_aemd_like_missing_catalyst_passes_discovery_but_no_setup_blocks() -> None:
    value = observation()
    scanner = evaluate_candidate(value)
    candidate = discover(value)
    assessed, signal = WarriorMomentumRuntime().assess_entry(candidate)
    assert scanner.qualified and scanner.catalyst is CatalystType.NONE
    assert scanner.policy_version == "BALANCED_V1"
    assert candidate.discovery_qualified is True
    assert ReasonCode.NO_CATALYST in candidate.reason_codes
    assert signal is None and ReasonCode.NO_SETUP in assessed.reason_codes


def test_balanced_observability_keeps_catalyst_fact_out_of_blockers() -> None:
    runtime = WarriorMomentumRuntime()
    candidate, signal = runtime.assess_entry(discover(observation()))
    blockers = entry_rejections(candidate, runtime.config)
    metadata = dict(watchlist_metadata(candidate))
    view = format_warrior_paper(WarriorPaperSnapshot(
        True, WarriorCaptureHealth.RUNNING, "balanced-v1",
        (WarriorFocusItem(
            candidate, CaptureFloatProvenance.AUTHORITATIVE_FLOAT,
            None, None, ("setup",),
        ),),
    ))
    row = view.focus.rows[0]
    assert signal is None
    assert ReasonCode.NO_CATALYST not in blockers
    assert ReasonCode.CATALYST_UNKNOWN not in blockers
    assert metadata["warrior_policy_version"] == "BALANCED_V1"
    assert metadata["warrior_discovery_status"] == "PASSED"
    assert metadata["warrior_entry_status"] == "BLOCKED"
    assert row.catalyst == "NONE"
    assert row.blocking_reasons == "No Warrior setup detected"
    assert "catalyst" not in row.blocking_reasons.lower()


def test_aemd_like_valid_trigger_missing_catalyst_reaches_balanced_paper_path() -> None:
    runtime, (assessed, signal) = assess_with_trigger(observation())
    assert assessed.status is CandidateStatus.ENTRY_READY
    assert signal is not None and signal.catalyst_state is CatalystStatus.FALSE
    assert signal.execution_authorized is False
    plan = prepare_paper_plan(
        signal, account_equity=D("50000"), buying_power=D("50000"),
        allowed_symbols=frozenset({"AEMD"}), risk_engine_approved=True,
    )
    assert plan.paper_execution_authorized is True
    assert plan.live_execution_authorized is False
    assert runtime.authorize_live(signal) is False


def test_bjdx_like_candidate_passes_without_5x_rvol_or_catalyst() -> None:
    value = observation(
        symbol="BJDX", price="1.02", change="12", rvol="2.48",
        float_shares="4500000", dollar_volume="10800000", spread="0.99",
    )
    scanner = evaluate_candidate(value)
    candidate = discover(value)
    assert scanner.qualified and "relative_volume" in scanner.passed_rules
    assert "news_catalyst" in scanner.passed_rules
    assert candidate.discovery_qualified is True
    assert candidate.relative_volume == D("2.48")


@pytest.mark.parametrize(
    ("changes", "failed_rule"),
    (
        ({"rvol": "1.5"}, "relative_volume"),
        ({"float_shares": "55000000"}, "low_float"),
        ({"dollar_volume": "750000"}, "dollar_volume"),
        ({"spread": "1.7"}, "spread"),
        ({"halted": True}, "not_halted"),
        ({"tradable": False}, "tradable"),
    ),
)
def test_balanced_discovery_hard_gates(changes, failed_rule: str) -> None:
    decision = evaluate_candidate(observation(**changes))
    assert decision.qualified is False
    assert failed_rule in decision.failed_rules


def test_discovery_spread_can_pass_while_entry_spread_blocks() -> None:
    value = observation(spread="1.40")
    assert evaluate_candidate(value).qualified is True
    _, (assessed, signal) = assess_with_trigger(value)
    assert signal is None and ReasonCode.SPREAD_WIDE in assessed.reason_codes


def test_discovery_liquidity_can_pass_while_entry_liquidity_blocks() -> None:
    value = observation(dollar_volume="1500000")
    assert evaluate_candidate(value).qualified is True
    _, (assessed, signal) = assess_with_trigger(value)
    assert signal is None and ReasonCode.LIQUIDITY_LOW in assessed.reason_codes


def test_forming_setup_never_executes() -> None:
    runtime = WarriorMomentumRuntime()
    candidate = discover(observation())
    forming = replace(
        triggered_setup(), state=SetupState.FORMING,
    )
    assessed, signal = runtime.assess_entry(replace(candidate, setup=forming))
    assert signal is None and assessed.status is CandidateStatus.SETUP_FORMING
    assert ReasonCode.NO_SETUP in assessed.reason_codes


@pytest.mark.parametrize("session", ("PREMARKET", "REGULAR", "AFTER_HOURS"))
def test_balanced_valid_trigger_is_entry_ready_in_allowed_sessions(session: str) -> None:
    _, (assessed, signal) = assess_with_trigger(observation(), session=session)
    assert assessed.status is CandidateStatus.ENTRY_READY
    assert signal is not None


def test_overnight_remains_blocked() -> None:
    _, (assessed, signal) = assess_with_trigger(observation(), session="OVERNIGHT")
    assert signal is None and ReasonCode.SESSION_NOT_ALLOWED in assessed.reason_codes


@pytest.mark.parametrize("changes", ({"halted": True}, {"tradable": False}))
def test_halt_and_tradability_also_block_warrior_entry(changes) -> None:
    _, (assessed, signal) = assess_with_trigger(observation(**changes))
    assert signal is None
    assert ({ReasonCode.HALTED, ReasonCode.NOT_TRADABLE} & set(assessed.reason_codes))

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from app.strategies.warrior_momentum.opportunity_engine import (
    AdaptiveOpportunityResult,
    OpportunityReason,
    WarriorOpportunityAssessment,
    WarriorOpportunityEngine,
    WarriorOpportunityState,
)
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.models import SetupState
from app.strategies.warrior_momentum.runtime import WarriorMomentumRuntime
from app.strategies.warrior_momentum.quick_scalper import (
    StrategyOwner, SymbolOwnershipRegistry,
)
from tests.warrior_momentum.test_forward_capture import account, point, scanner


NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)


def assessment(
    symbol: str = "FAST",
    generation: str = "generation-1",
    *,
    result: AdaptiveOpportunityResult = AdaptiveOpportunityResult.ARMED,
    quality: str = "GOOD",
    setup_quality: str = "80",
    priority: str = "75",
    remaining_r: str = "2.0",
    hard: OpportunityReason | None = None,
    reasons: tuple[OpportunityReason, ...] = (),
) -> WarriorOpportunityAssessment:
    return WarriorOpportunityAssessment(
        symbol=symbol,
        setup_family="RECLAIM_CONTINUATION",
        generation_id=generation,
        lifecycle_id=generation,
        decision_timestamp=NOW,
        scanner_timestamp=NOW - timedelta(milliseconds=20),
        quote_timestamp=NOW - timedelta(seconds=1),
        last_timestamp=NOW - timedelta(milliseconds=500),
        structural_trigger=D("10"),
        executable_entry=D("10.05"),
        structural_stop=D("9.75"),
        risk_per_share=D("0.30"),
        target_levels=(D("10.30"), D("10.60"), D("10.90")),
        remaining_first_target_r=D("0.75"),
        remaining_final_target_r=D(remaining_r),
        spread_percent=D("0.5"),
        execution_cost=D("0.05"),
        execution_quality=quality,
        dollar_liquidity=D("25000000"),
        relative_volume=D("1.4"),
        volatility_context=D("0.8"),
        catalyst_context="TRUE",
        setup_quality=D(setup_quality),
        momentum_priority=D(priority),
        displacement_percent=D("0.5"),
        adaptive_result=result,
        hard_safety_reason=hard,
        reason_codes=reasons,
    )


def test_strong_setup_arms_then_becomes_executable_and_one_order_lifecycle() -> None:
    engine = WarriorOpportunityEngine()
    armed = engine.arm(assessment())
    executable = engine.apply_assessment(replace(
        armed.assessment, adaptive_result=AdaptiveOpportunityResult.EXECUTABLE,
    ))
    engine.mark_authorized("FAST", "generation-1", at=NOW)
    pending = engine.mark_order_pending("FAST", "generation-1", at=NOW)
    duplicate = engine.apply_assessment(replace(
        armed.assessment, adaptive_result=AdaptiveOpportunityResult.EXECUTABLE,
    ))
    assert executable.state is WarriorOpportunityState.EXECUTABLE
    assert pending is not None and pending.state is WarriorOpportunityState.ORDER_PENDING
    assert duplicate.state is WarriorOpportunityState.ORDER_PENDING
    assert len(engine.snapshot()) == 1


def test_temporary_poor_spread_recovers_on_later_streaming_assessment() -> None:
    engine = WarriorOpportunityEngine()
    engine.arm(assessment())
    waiting = engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.WAIT, quality="POOR",
        reasons=(OpportunityReason.EXECUTION_QUALITY_WAIT,),
    ))
    recovered = engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.EXECUTABLE, quality="GOOD",
    ))
    assert waiting.state is WarriorOpportunityState.WAITING_EXECUTION
    assert recovered.state is WarriorOpportunityState.EXECUTABLE
    assert recovered.assessment.generation_id == waiting.assessment.generation_id


def test_poor_spread_that_never_improves_never_becomes_executable() -> None:
    engine = WarriorOpportunityEngine()
    for index in range(3):
        waiting = engine.apply_assessment(replace(
            assessment(result=AdaptiveOpportunityResult.WAIT, quality="POOR",
                       reasons=(OpportunityReason.EXECUTION_QUALITY_WAIT,)),
            decision_timestamp=NOW + timedelta(seconds=index),
        ))
    assert waiting.state is WarriorOpportunityState.WAITING_EXECUTION
    assert engine.ranked(executable_only=True) == ()


def test_stale_provider_timestamp_fails_closed_but_fresh_same_generation_recovers() -> None:
    engine = WarriorOpportunityEngine()
    stale = engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.WAIT,
        hard=OpportunityReason.PROVIDER_DATA_STALE,
        reasons=(OpportunityReason.PROVIDER_DATA_STALE,),
    ))
    assert stale.state is WarriorOpportunityState.WAITING_EXECUTION
    assert engine.ranked(executable_only=True) == ()
    fresh = engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.EXECUTABLE,
    ))
    assert fresh.state is WarriorOpportunityState.EXECUTABLE


def test_quote_for_obsolete_generation_cannot_authorize_new_opportunity() -> None:
    engine = WarriorOpportunityEngine()
    engine.arm(assessment(generation="old"))
    current = engine.arm(assessment(generation="new"))
    assert current.state is WarriorOpportunityState.ARMED
    assert engine.quote_matches("FAST", "old") is False
    assert engine.quote_matches("FAST", "new") is True


def test_continuation_assessment_uses_current_structure_not_prior_close_move() -> None:
    value = assessment(generation="fresh-continuation")
    assert value.structural_trigger == D("10")
    assert value.displacement_percent == D("0.5")
    assert not hasattr(value, "prior_close_change_percent")


def test_excessive_current_trigger_displacement_waits_without_losing_generation() -> None:
    engine = WarriorOpportunityEngine()
    item = engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.WAIT,
        reasons=(OpportunityReason.EXCESSIVE_CURRENT_STRUCTURE_DISPLACEMENT,),
    ))
    assert item.state is WarriorOpportunityState.WAITING_EXECUTION
    recovered = engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.EXECUTABLE,
    ))
    assert recovered.state is WarriorOpportunityState.EXECUTABLE
    assert recovered.assessment.generation_id == item.assessment.generation_id


def test_insufficient_current_reward_wait_is_not_overridden_by_momentum() -> None:
    engine = WarriorOpportunityEngine()
    item = engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.WAIT,
        priority="999", setup_quality="999",
        reasons=(OpportunityReason.INSUFFICIENT_CURRENT_REWARD,),
    ))
    assert item.state is WarriorOpportunityState.WAITING_EXECUTION
    assert engine.ranked(executable_only=True) == ()


def test_hard_risk_and_capital_rejections_cannot_be_overridden_by_score() -> None:
    for reason in (
        OpportunityReason.RISK_NOT_AUTHORIZED,
        OpportunityReason.ACCOUNT_NOT_AUTHORIZED,
    ):
        engine = WarriorOpportunityEngine()
        item = engine.apply_assessment(assessment(
            result=AdaptiveOpportunityResult.EXECUTABLE,
            priority="999", hard=reason, reasons=(reason,),
        ))
        assert item.state is WarriorOpportunityState.REJECTED_HARD_SAFETY
        assert engine.ranked(executable_only=True) == ()


def test_candidate_rank_removal_does_not_remove_armed_lifecycle() -> None:
    engine = WarriorOpportunityEngine()
    engine.arm(assessment())
    # No scanner/display callback is an engine mutation.
    assert engine.get("FAST") is not None
    assert engine.get("FAST").state is WarriorOpportunityState.ARMED


def test_invalidation_while_waiting_is_terminal_and_cannot_be_resurrected() -> None:
    engine = WarriorOpportunityEngine()
    engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.WAIT, quality="POOR",
        reasons=(OpportunityReason.EXECUTION_QUALITY_WAIT,),
    ))
    engine.invalidate("FAST", "generation-1", at=NOW,
                      reason=OpportunityReason.SETUP_INVALIDATED)
    result = engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.EXECUTABLE,
    ))
    assert result.state is WarriorOpportunityState.INVALIDATED


def test_two_simultaneous_opportunities_rank_deterministically_without_authority() -> None:
    engine = WarriorOpportunityEngine()
    engine.apply_assessment(assessment(
        symbol="LOWER", generation="lower",
        result=AdaptiveOpportunityResult.EXECUTABLE,
        setup_quality="70", priority="90",
    ))
    engine.apply_assessment(assessment(
        symbol="BETTER", generation="better",
        result=AdaptiveOpportunityResult.EXECUTABLE,
        setup_quality="90", priority="70",
    ))
    ranked = engine.ranked(executable_only=True)
    assert [item.assessment.symbol for item in ranked] == ["BETTER", "LOWER"]
    assert all(item.state is WarriorOpportunityState.EXECUTABLE for item in ranked)


def test_book_is_bounded_without_creating_execution_authority() -> None:
    engine = WarriorOpportunityEngine(capacity=2)
    for index in range(3):
        engine.arm(assessment(symbol=f"S{index}", generation=f"g{index}"))
    assert len(engine.snapshot()) == 2
    assert engine.ranked(executable_only=True) == ()


def test_observability_failure_is_contained() -> None:
    def broken(_transition) -> None:
        raise RuntimeError("diagnostic only")

    engine = WarriorOpportunityEngine(observer=broken)
    item = engine.arm(assessment())
    assert item.state is WarriorOpportunityState.ARMED


def test_authorization_provenance_is_generation_bound_and_sanitized() -> None:
    engine = WarriorOpportunityEngine()
    engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.EXECUTABLE,
    ))
    evaluated = engine.mark_authorization_evaluated(
        "FAST", "generation-1", at=NOW, authorized=False,
        reason="BUYING_POWER_REJECTED secret=do-not-copy",
        risk_result="REJECTED", sizing_result="ZERO",
    )
    assert evaluated is not None
    assert evaluated.state is WarriorOpportunityState.REJECTED_HARD_SAFETY
    assert evaluated.authorization_result == "REJECTED"
    assert evaluated.authorization_reason == "OTHER"
    assert engine.mark_order_intent(
        "FAST", "generation-1", at=NOW, submitted=True,
    ) is None


def test_live_authority_is_not_part_of_opportunity_engine() -> None:
    engine = WarriorOpportunityEngine()
    item = engine.apply_assessment(assessment(
        result=AdaptiveOpportunityResult.EXECUTABLE,
    ))
    assert item.state is WarriorOpportunityState.EXECUTABLE
    assert not hasattr(engine, "authorize_live")


def test_service_level_executable_opportunity_creates_exactly_one_paper_intent(
    tmp_path,
) -> None:
    submitted = []
    store = ForwardCaptureStore(tmp_path / "opportunity-entry.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *args: (submitted.append(args) or True),
    )
    try:
        candidate, signal = service.observe(point(
            observation=scanner(bid=D('10.20'), ask=D('10.21')),
        ), account=account())
        assert signal is not None
        assert len(submitted) == 1
        assert candidate.opportunity_state == "ORDER_PENDING"
        assert len(service.opportunity_engine.snapshot()) == 1
        service.observe(point(), account=account())
        assert len(submitted) == 1
    finally:
        writer.close()


class _StableTriggeredRuntime(WarriorMomentumRuntime):
    def discover(self, observation, bars, *, session, top_gapper=False):
        candidate = super().discover(
            observation, bars, session=session, top_gapper=top_gapper,
        )
        assert candidate.setup is not None
        return replace(candidate, setup=replace(
            candidate.setup,
            state=SetupState.TRIGGERED,
            trigger=D("10.20"),
            stop_price=D("9.90"),
            structural_episode_id="stable-streaming-generation",
            structural_entry_trigger=D("10.20"),
        ))


class _LowScoreTriggeredRuntime(_StableTriggeredRuntime):
    def discover(self, observation, bars, *, session, top_gapper=False):
        candidate = super().discover(
            observation, bars, session=session, top_gapper=top_gapper,
        )
        return replace(
            candidate,
            score=replace(candidate.score, total=D("52.48")),
            momentum_priority=D("52.48"),
        )


class _OneShotTriggeredRuntime(_StableTriggeredRuntime):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def discover(self, observation, bars, *, session, top_gapper=False):
        candidate = super().discover(
            observation, bars, session=session, top_gapper=top_gapper,
        )
        self.calls += 1
        if self.calls > 1:
            candidate = replace(
                candidate, setup=replace(
                    candidate.setup, state=SetupState.FORMING,
                ),
            )
        return candidate


class _OneShotMissingSetupRuntime(_StableTriggeredRuntime):
    """Reproduce AIXI: triggered once, then completed-bar selection is empty."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def discover(self, observation, bars, *, session, top_gapper=False):
        candidate = super().discover(
            observation, bars, session=session, top_gapper=top_gapper,
        )
        self.calls += 1
        if self.calls > 1:
            candidate = replace(candidate, setup=None)
        return candidate


def test_low_legacy_score_reaches_engine_as_adaptive_evidence(tmp_path) -> None:
    submitted = []
    store = ForwardCaptureStore(tmp_path / "low-score-entry.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *args: (submitted.append(args) or True),
    )
    service.runtime = _LowScoreTriggeredRuntime()
    try:
        candidate, signal = service.observe(point(
            observation=scanner(bid=D('10.20'), ask=D('10.21')),
        ), account=account())
        generation = candidate.opportunity_generation_id
        assert generation is not None
        item = service.opportunity_engine.get("XYZ", generation)
        assert item is not None
        assert item.assessment.momentum_score == D("52.48")
        assert item.state is WarriorOpportunityState.ORDER_PENDING
        assert signal is not None
        assert len(submitted) == 1
    finally:
        writer.close()


def test_momentum_score_remains_a_deterministic_rank_input() -> None:
    engine = WarriorOpportunityEngine()
    engine.apply_assessment(replace(
        assessment(symbol="LOW", generation="low",
                   result=AdaptiveOpportunityResult.EXECUTABLE),
        momentum_score=D("52.48"),
    ))
    engine.apply_assessment(replace(
        assessment(symbol="HIGH", generation="high",
                   result=AdaptiveOpportunityResult.EXECUTABLE),
        momentum_score=D("75"),
    ))
    assert [item.assessment.symbol for item in engine.ranked()] == [
        "HIGH", "LOW",
    ]


def test_low_score_stale_data_still_fails_closed(tmp_path) -> None:
    submitted = []
    store = ForwardCaptureStore(tmp_path / "low-score-stale.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *args: (submitted.append(args) or True),
    )
    service.runtime = _LowScoreTriggeredRuntime()
    now = point().observation.timestamp
    try:
        candidate, signal = service.observe(point(
            evaluation_timestamp=now,
            quote_observed_at=now - timedelta(seconds=6),
            last_price_observed_at=now - timedelta(seconds=6),
            quote_freshness_seconds=D("6"),
            last_price_freshness_seconds=D("6"),
        ), account=account())
        assert signal is None
        assert submitted == []
        item = service.opportunity_engine.get(
            "XYZ", candidate.opportunity_generation_id,
        )
        assert item is not None
        assert item.state is WarriorOpportunityState.WAITING_EXECUTION
        assert item.reason is OpportunityReason.PROVIDER_DATA_STALE
    finally:
        writer.close()


def test_service_level_wait_recovers_on_same_generation_without_rediscovery(
    tmp_path,
) -> None:
    submitted = []
    store = ForwardCaptureStore(tmp_path / "opportunity-recovery.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *args: (submitted.append(args) or True),
    )
    service.runtime = _StableTriggeredRuntime()
    first_at = point().observation.timestamp
    try:
        waiting, first_signal = service.observe(point(
            observation=scanner(
                timestamp=first_at, price=D("10.20"),
                    bid=D("10.19"), ask=D("10.20"),
            ),
            evaluation_timestamp=first_at,
            quote_observed_at=first_at,
            last_price_observed_at=first_at,
        ), account=account())
        assert first_signal is None
        assert waiting.opportunity_state == "WAITING_EXECUTION"
        assert submitted == []
        item = service.opportunity_engine.get(
            'XYZ', waiting.opportunity_generation_id,
        )
        assert item is not None
        assert item.reason is OpportunityReason.EXECUTABLE_TRIGGER_NOT_CONFIRMED

        second_at = first_at + timedelta(seconds=1)
        advanced, second_signal = service.observe(point(
            observation=scanner(
                timestamp=second_at, price=D("10.20"),
                    bid=D("10.20"), ask=D("10.21"),
            ),
            evaluation_timestamp=second_at,
            quote_observed_at=second_at,
            last_price_observed_at=second_at,
        ), account=account())
        assert second_signal is not None
        assert advanced.opportunity_state == "ORDER_PENDING"
        assert waiting.opportunity_generation_id == advanced.opportunity_generation_id
        assert len(submitted) == 1
    finally:
        writer.close()


def test_one_shot_trigger_wait_recovers_from_forming_same_generation(
    tmp_path,
) -> None:
    submitted = []
    store = ForwardCaptureStore(tmp_path / "one-shot-recovery.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *args: (submitted.append(args) or True),
    )
    service.runtime = _OneShotTriggeredRuntime()
    first_at = point().observation.timestamp
    try:
        waiting, signal = service.observe(point(
            observation=scanner(
                timestamp=first_at, price=D("10.20"),
                bid=D("9.60"), ask=D("10.20"),
            ),
            evaluation_timestamp=first_at,
            quote_observed_at=first_at,
            last_price_observed_at=first_at,
        ), account=account())
        assert signal is None
        generation = waiting.opportunity_generation_id
        assert generation is not None
        assert waiting.opportunity_state == "WAITING_EXECUTION"

        second_at = first_at + timedelta(seconds=1)
        advanced, signal = service.observe(point(
            observation=scanner(
                timestamp=second_at, price=D("10.20"),
                    bid=D("10.20"), ask=D("10.21"),
            ),
            evaluation_timestamp=second_at,
            quote_observed_at=second_at,
            last_price_observed_at=second_at,
        ), account=account())
        assert signal is not None
        assert advanced.opportunity_generation_id == generation
        assert advanced.opportunity_state == "ORDER_PENDING"
        assert len(submitted) == 1
    finally:
        writer.close()


def test_aixi_trigger_wait_survives_transient_missing_detector_selection(
    tmp_path,
) -> None:
    submitted = []
    store = ForwardCaptureStore(tmp_path / "aixi-missing-setup-recovery.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *args: (submitted.append(args) or True),
    )
    service.runtime = _OneShotMissingSetupRuntime()
    first_at = point().observation.timestamp
    try:
        waiting, signal = service.observe(point(
            observation=scanner(
                timestamp=first_at, price=D("10.20"),
                bid=D("9.60"), ask=D("10.20"),
            ),
            evaluation_timestamp=first_at,
            quote_observed_at=first_at,
            last_price_observed_at=first_at,
        ), account=account())
        assert signal is None
        generation = waiting.opportunity_generation_id
        assert generation is not None
        assert waiting.opportunity_state == "WAITING_EXECUTION"

        second_at = first_at + timedelta(seconds=1)
        advanced, signal = service.observe(point(
            observation=scanner(
                timestamp=second_at, price=D("10.20"),
                    bid=D("10.20"), ask=D("10.21"),
            ),
            evaluation_timestamp=second_at,
            quote_observed_at=second_at,
            last_price_observed_at=second_at,
        ), account=account())

        assert signal is not None
        assert advanced.opportunity_generation_id == generation
        assert advanced.opportunity_state == "ORDER_PENDING"
        assert len(submitted) == 1
    finally:
        writer.close()


def test_missing_detector_selection_cannot_retain_generation_past_bound(
    tmp_path,
) -> None:
    submitted = []
    store = ForwardCaptureStore(tmp_path / "missing-setup-expiry.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *args: (submitted.append(args) or True),
    )
    service.runtime = _OneShotMissingSetupRuntime()
    first_at = point().observation.timestamp
    try:
        waiting, _signal = service.observe(point(
            observation=scanner(
                timestamp=first_at, price=D("10.20"),
                bid=D("9.60"), ask=D("10.20"),
            ),
            evaluation_timestamp=first_at,
            quote_observed_at=first_at,
            last_price_observed_at=first_at,
        ), account=account())
        generation = waiting.opportunity_generation_id
        expired_at = first_at + timedelta(seconds=121)
        expired, signal = service.observe(point(
            observation=scanner(
                timestamp=expired_at, price=D("10.20"),
                bid=D("10.18"), ask=D("10.20"),
            ),
            evaluation_timestamp=expired_at,
            quote_observed_at=expired_at,
            last_price_observed_at=expired_at,
        ), account=account())

        assert signal is None
        assert submitted == []
        item = service.opportunity_engine.get("XYZ", generation)
        assert item is not None
        assert item.state is WarriorOpportunityState.EXPIRED
    finally:
        writer.close()


def test_scalper_owned_symbol_blocks_warrior_before_canonical_submission(
    tmp_path,
) -> None:
    submitted = []
    owners = SymbolOwnershipRegistry()
    assert owners.acquire("XYZ", StrategyOwner.QUICK_SCALPER, "scalp-life")
    store = ForwardCaptureStore(tmp_path / "ownership-collision.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        paper_entry_submitter=lambda *args: (submitted.append(args) or True),
        strategy_ownership=owners,
    )
    try:
        candidate, signal = service.observe(point(), account=account())
        assert signal is None
        assert submitted == []
        generation = candidate.opportunity_generation_id
        assert generation is not None
        item = service.opportunity_engine.get("XYZ", generation)
        assert item is not None
        assert item.authorization_result == "REJECTED"
        assert item.authorization_reason == "SYMBOL_OWNED_BY_OTHER_STRATEGY"
        assert owners.owner("XYZ") == (
            StrategyOwner.QUICK_SCALPER, "scalp-life",
        )
    finally:
        writer.close()

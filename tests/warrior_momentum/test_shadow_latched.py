from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from app.strategies.warrior_momentum import (
    CaptureRecord,
    CaptureRecordType,
    ForwardCaptureWriter,
    ForwardCaptureStore,
    PaperAccountContext,
    ReasonCode,
    SetupState,
    ShadowLatchedPlanResearch,
    ShadowLatchedTransition,
    ShadowMarketObservation,
    WarriorForwardCaptureService,
    WarriorMomentumRuntime,
    analyze_shadow_latched,
    analyze_shadow_latched_store,
)
from tests.warrior_momentum.test_forward_capture import point, scanner

T0 = datetime(2026, 8, 28, 22, 32, 0, 44000, tzinfo=UTC)
TRIGGER = D("6.083040")
STOP = D("5.93")


def spread(bid: str, ask: str) -> D:
    bid_value = D(bid)
    ask_value = D(ask)
    return (ask_value - bid_value) / ((ask_value + bid_value) / D("2")) * D("100")


def observation(
    at: datetime,
    *,
    last: str,
    bid: str,
    ask: str,
    session: str = "AFTER_HOURS",
    halted: bool = False,
    tradable: bool = True,
    execution_permitted: bool = True,
    provider_at: datetime | None = None,
) -> ShadowMarketObservation:
    source_at = provider_at or at
    return ShadowMarketObservation(
        symbol="AEHL",
        observed_at=at,
        last=D(last),
        bid=D(bid),
        ask=D(ask),
        last_timestamp=source_at,
        quote_timestamp=source_at,
        last_received_timestamp=at,
        quote_received_timestamp=at,
        halted=halted,
        tradable=tradable,
        session=session,
        execution_permitted=execution_permitted,
    )


def technical_case(*, setup_state: SetupState = SetupState.TRIGGERED):
    runtime = WarriorMomentumRuntime()
    base_value = point()
    base = runtime.discover(
        base_value.observation, base_value.bars, session=base_value.session,
    )
    assert base.setup is not None
    setup = replace(
        base.setup,
        state=setup_state,
        trigger=TRIGGER if setup_state is SetupState.TRIGGERED else None,
        stop_price=STOP if setup_state is SetupState.TRIGGERED else None,
    )
    current = scanner(
        symbol="AEHL",
        timestamp=T0,
        price=D("6.17"),
        bid=D("6.05"),
        ask=D("6.17"),
        last_price_timestamp=T0,
        quote_timestamp=T0,
        last_price_received_timestamp=T0,
        quote_received_timestamp=T0,
    )
    candidate = replace(
        base,
        symbol="AEHL",
        timestamp=T0,
        price=D("6.17"),
        spread_percent=spread("6.05", "6.17"),
        setup=setup,
        session="AFTER_HOURS",
        bid=D("6.05"),
        ask=D("6.17"),
        reason_codes=(ReasonCode.SPREAD_WIDE,),
    )
    value = replace(
        base_value,
        observation=current,
        session="AFTER_HOURS",
        bars=tuple(
            replace(bar, symbol="AEHL", timestamp=T0 - timedelta(minutes=5-index))
            for index, bar in enumerate(base_value.bars)
        ),
        quote_observed_at=T0,
        last_price_observed_at=T0,
        quote_freshness_seconds=D("0"),
        last_price_freshness_seconds=D("0"),
        evaluation_timestamp=T0,
    )
    return candidate, runtime.technical_entry_signal(candidate), value


def create_plan(
    tracker: ShadowLatchedPlanResearch,
    *,
    account_context: PaperAccountContext | None = None,
):
    candidate, signal, value = technical_case()
    return tracker.create(
        candidate,
        signal,
        value,
        decision_record_id="aehl-decision-t0",
        entry_rejections=(ReasonCode.SPREAD_WIDE,),
        account=(account_context if account_context is not None else PaperAccountContext(
            D("50000"), D("25000"), frozenset({"AEHL"}),
        )),
        existing_strategy_position=False,
        execution_permitted=True,
    )


def transitions(records: tuple[CaptureRecord, ...]) -> list[str]:
    return [
        str(record.payload["transition"])
        for record in records
        if record.record_type is CaptureRecordType.SHADOW_LATCHED_TRANSITION
    ]


def test_aehl_latched_plan_reconstructs_fast_execution_transitions_without_order() -> None:
    tracker = ShadowLatchedPlanResearch()
    records = list(create_plan(tracker))
    plan = next(record for record in records if record.record_type is CaptureRecordType.SHADOW_LATCHED_PLAN)
    assert plan.payload["authority"] == "SHADOW_ONLY_NON_EXECUTABLE"
    assert plan.payload["trigger"] == str(TRIGGER)
    assert plan.payload["stop"] == str(STOP)
    assert plan.payload["original_market"]["spread_percent"] == str(spread("6.05", "6.17"))
    assert transitions(tuple(records)) == [
        "PLAN_CREATED", "SPREAD_BLOCKED", "QUOTE_FRESH",
        "MARKET_BLOCKED", "LIMIT_NOT_MARKETABLE",
    ]

    clear_at = T0 + timedelta(seconds=1, milliseconds=353)
    clear = tracker.observe(observation(
        clear_at, last="6.17", bid="6.15", ask="6.19",
    ))
    records.extend(clear)
    assert transitions(clear) == [
        "SPREAD_CLEAR", "MARKET_ELIGIBLE",
        "HYPOTHETICAL_ORDER_AUTHORIZED",
    ]
    eligible_snapshot = next(
        record.payload for record in clear
        if record.payload.get("transition") == "MARKET_ELIGIBLE"
    )
    assert eligible_snapshot["market_eligible"] is True
    assert eligible_snapshot["limit_marketable"] is False
    assert tracker.observe(observation(
        clear_at, last="6.17", bid="6.15", ask="6.19",
    )) == ()

    marketable_wide_at = T0 + timedelta(seconds=31, milliseconds=654)
    marketable_wide = tracker.observe(observation(
        marketable_wide_at, last="6.02", bid="5.96", ask="6.08",
    ))
    records.extend(marketable_wide)
    assert transitions(marketable_wide) == [
        "SPREAD_BLOCKED", "MARKET_REBLOCKED", "LIMIT_MARKETABLE",
        "HYPOTHETICAL_FILL_POSSIBLE",
    ]
    assert next(
        record.payload["policy"] for record in marketable_wide
        if record.payload.get("transition") == "HYPOTHETICAL_FILL_POSSIBLE"
    ) == "A_AUTHORIZATION_SPREAD_ONLY"

    eligible_marketable_at = T0 + timedelta(seconds=36, milliseconds=560)
    eligible_marketable = tracker.observe(observation(
        eligible_marketable_at, last="5.96", bid="5.95", ask="6.00",
    ))
    records.extend(eligible_marketable)
    assert transitions(eligible_marketable) == [
        "SPREAD_CLEAR", "MARKET_ELIGIBLE", "HYPOTHETICAL_FILL_POSSIBLE",
    ]
    assert next(
        record.payload["policy"] for record in eligible_marketable
        if record.payload.get("transition") == "HYPOTHETICAL_FILL_POSSIBLE"
    ) == "B_SPREAD_THROUGH_FILL"

    closed = tracker.invalidate(
        "AEHL", T0 + timedelta(minutes=1),
        ShadowLatchedTransition.NEW_BAR_INVALIDATION,
        reason="NEW_COMPLETED_BAR",
    )
    records.extend(closed)
    assert transitions(closed) == ["NEW_BAR_INVALIDATION", "PLAN_EXPIRED"]
    outcome = next(
        record.payload for record in closed
        if record.record_type is CaptureRecordType.SHADOW_LATCHED_OUTCOME
    )
    assert outcome["time_to_clear_seconds"] == "1.353"
    assert outcome["clear_cycles"] == 2
    assert outcome["reblock_cycles"] == 1
    assert outcome["policy_a_fill_possible"] is True
    assert outcome["policy_b_fill_possible"] is True
    assert outcome["hypothetical_fill_claimed"] is False
    assert outcome["paper_submission_attempted"] is False

    summary = analyze_shadow_latched(tuple(records))
    assert summary.total_plans == 1
    assert summary.initially_blocked_plans == 1
    assert summary.blocker_cleared_plans == 1
    assert summary.p50_time_to_clear_seconds == D("1.353")
    assert summary.p90_time_to_clear_seconds == D("1.353")
    assert summary.clear_cycles == 2
    assert summary.reblock_cycles == 1
    assert summary.limit_marketable_plans == 1
    assert summary.account_approved_plans == 1
    assert summary.policy_a_fill_possible == 1
    assert summary.policy_b_fill_possible == 1
    assert summary.hypothetical_order_possible_plans == 1
    assert summary.new_bar_invalidations == 1

    outcome_records = tuple(records) + (
        CaptureRecord.create(
            CaptureRecordType.SHADOW_OUTCOME,
            "AEHL",
            T0 + timedelta(minutes=1),
            {
                "decision_record_id": "aehl-decision-t0",
                "horizon_minutes": 1,
                "mfe_percent": D("2.1"),
                "mae_percent": D("-0.8"),
                "hypothetical_trade": {
                    "mfe_r": D("1.2"), "mae_r": D("-0.4"),
                },
            },
        ),
    )
    assert analyze_shadow_latched(outcome_records).correlated_outcomes == ((1, 1),)


def test_plan_requires_triggered_candidate_and_only_execution_variable_blockers() -> None:
    tracker = ShadowLatchedPlanResearch()
    for state in (SetupState.NOT_FORMED, SetupState.FORMING):
        candidate, signal, value = technical_case(setup_state=state)
        assert tracker.create(
            candidate, signal, value,
            decision_record_id=f"decision-{state.value}",
            entry_rejections=(ReasonCode.SPREAD_WIDE,),
            account=None,
            existing_strategy_position=False,
            execution_permitted=True,
        ) == ()
    candidate, signal, value = technical_case()
    assert tracker.create(
        candidate, signal, value,
        decision_record_id="static-blocker",
        entry_rejections=(ReasonCode.SPREAD_WIDE, ReasonCode.RVOL_LOW),
        account=None,
        existing_strategy_position=False,
        execution_permitted=True,
    ) == ()
    assert tracker.active_symbols == ()


def test_plan_identity_and_geometry_are_frozen_across_market_observations() -> None:
    tracker = ShadowLatchedPlanResearch()
    created = create_plan(tracker)
    payload = next(
        record.payload for record in created
        if record.record_type is CaptureRecordType.SHADOW_LATCHED_PLAN
    )
    plan_id = payload["plan_id"]
    original_bar_version = payload["technical_bar_version"]
    tracker.observe(observation(
        T0 + timedelta(seconds=2), last="7.00", bid="6.99", ask="7.00",
    ))
    state = tracker._active["AEHL"]  # Research state is inspected to prove immutability.
    assert state.plan.plan_id == plan_id
    assert state.plan.trigger == TRIGGER
    assert state.plan.stop == STOP
    assert [item.isoformat() for item in state.plan.technical_bar_version] == original_bar_version


def test_stale_quote_records_confirmation_need_but_never_requests_rest() -> None:
    tracker = ShadowLatchedPlanResearch()
    create_plan(tracker)
    at = T0 + timedelta(seconds=10)
    records = tracker.observe(observation(
        at, last="6.08", bid="6.07", ask="6.08", provider_at=T0,
    ))
    assert "QUOTE_STALE" in transitions(records)
    for record in records:
        assert record.payload["rest_confirmation_requested"] is False
        assert record.payload["market"]["confirmation_would_be_required"] is True
        assert record.payload["market"]["confirmation_reason"] == "STREAMING_EXECUTION_DATA_STALE"


def test_unavailable_account_is_recorded_without_hypothetical_authorization() -> None:
    tracker = ShadowLatchedPlanResearch()
    candidate, signal, value = technical_case()
    records = tracker.create(
        candidate, signal, value,
        decision_record_id="no-account",
        entry_rejections=(ReasonCode.SPREAD_WIDE,),
        account=None,
        existing_strategy_position=False,
        execution_permitted=True,
    )
    assert "ACCOUNT_CONTEXT_UNAVAILABLE" in transitions(records)
    clear = tracker.observe(observation(
        T0 + timedelta(seconds=1), last="6.17", bid="6.15", ask="6.19",
    ))
    assert "MARKET_ELIGIBLE" in transitions(clear)
    assert "HYPOTHETICAL_ORDER_AUTHORIZED" not in transitions(clear)


def test_shutdown_invalidates_once_and_prevents_late_research_activity() -> None:
    tracker = ShadowLatchedPlanResearch()
    create_plan(tracker)
    closed = tracker.shutdown(T0 + timedelta(seconds=5))
    assert transitions(closed) == ["PLAN_EXPIRED"]
    assert tracker.shutdown(T0 + timedelta(seconds=6)) == ()
    assert tracker.observe(observation(
        T0 + timedelta(seconds=7), last="6.08", bid="6.07", ask="6.08",
    )) == ()
    assert create_plan(tracker) == ()


def test_new_record_types_round_trip_through_existing_wal_store(tmp_path: Path) -> None:
    tracker = ShadowLatchedPlanResearch()
    records = create_plan(tracker)
    store = ForwardCaptureStore(tmp_path / "shadow-latched.sqlite3")
    inserted, duplicates = store.append_batch(records)
    assert inserted == len(records)
    assert duplicates == 0
    restored = store.records(symbol="AEHL")
    assert [record.record_type for record in restored] == [
        CaptureRecordType.SHADOW_LATCHED_PLAN,
        CaptureRecordType.SHADOW_LATCHED_TRANSITION,
        CaptureRecordType.SHADOW_LATCHED_TRANSITION,
        CaptureRecordType.SHADOW_LATCHED_TRANSITION,
        CaptureRecordType.SHADOW_LATCHED_TRANSITION,
        CaptureRecordType.SHADOW_LATCHED_TRANSITION,
    ]
    assert store.integrity_check() == "ok"
    assert analyze_shadow_latched_store(store).total_plans == 1


def test_forward_service_shadow_path_cannot_submit_paper_or_request_rest(
    tmp_path: Path,
) -> None:
    paper_submissions: list[object] = []
    rest_requests: list[str] = []

    def paper_submit(*values) -> bool:
        paper_submissions.append(values)
        return True

    def execution_quote(symbol: str):
        rest_requests.append(symbol)
        raise AssertionError("shadow observation must not request REST")

    store = ForwardCaptureStore(tmp_path / "isolation.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store,
        writer,
        paper_entry_submitter=paper_submit,
        execution_quote_source=execution_quote,
    )
    base = point()
    at = base.observation.timestamp
    wide = replace(
        base,
        observation=replace(
            base.observation,
            bid=D("10.00"),
            ask=D("10.30"),
            last_price_timestamp=at,
            quote_timestamp=at,
        ),
        quote_observed_at=at,
        last_price_observed_at=at,
        quote_freshness_seconds=D("0"),
        last_price_freshness_seconds=D("0"),
        evaluation_timestamp=at,
    )
    _, production_signal = service.observe(
        wide,
        account=PaperAccountContext(D("50000"), D("25000"), frozenset({"XYZ"})),
    )
    assert production_signal is None
    service.observe_intraminute_shadow(ShadowMarketObservation(
        symbol="XYZ",
        observed_at=at + timedelta(seconds=10),
        last=D("10.10"),
        bid=D("10.09"),
        ask=D("10.10"),
        last_timestamp=at,
        quote_timestamp=at,
        last_received_timestamp=at,
        quote_received_timestamp=at,
        halted=False,
        tradable=True,
        session="REGULAR",
        execution_permitted=True,
    ))
    writer.flush()
    writer.close()

    assert paper_submissions == []
    assert rest_requests == []
    assert store.records(record_type=CaptureRecordType.PAPER_FILL) == ()
    plans = store.records(record_type=CaptureRecordType.SHADOW_LATCHED_PLAN)
    assert len(plans) == 1
    assert plans[0].payload["production_signal_mutated"] is False
    assert plans[0].payload["paper_submission_attempted"] is False
    stale = [
        record for record in store.records(
            record_type=CaptureRecordType.SHADOW_LATCHED_TRANSITION,
        )
        if record.payload.get("transition") == "QUOTE_STALE"
    ]
    assert len(stale) == 1
    assert stale[0].payload["rest_confirmation_requested"] is False

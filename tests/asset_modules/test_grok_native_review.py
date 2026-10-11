"""Pending review never masquerades as submission or releases uncertainty."""
from dataclasses import replace
from datetime import timedelta
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_native_review import CurrentEntryAuthorization, NativeGrokReview
from app.asset_modules.grok_paper_entries import EntryDispatch, EntryOutcome
from app.strategies.warrior_momentum.autonomous_paper import lifecycle_identity
from tests.asset_modules.test_controlled_paper_service import setup, NOW
from tests.asset_modules.test_grok_paper_entries import suite
from tests.asset_modules.test_grok_runtime_handoff import signal, AUTH, FloatProvenance, warrior, scalper
from tests.asset_modules.test_native_entry_preparation import signal as scalp_signal


def value(engine):
    return signal() if engine is EngineId.WARRIOR else scalp_signal(engine)


def grant(v, **changes):
    return replace(CurrentEntryAuthorization(lifecycle_identity(v), NOW,
        NOW + timedelta(seconds=60), True, True, True, True, True), **changes)


def create(setup, transform=None):
    _, bridge, service, _, _, clock = setup
    ai, _ = suite(transform)
    ai._clock = lambda: clock[0]
    ai.master._clock = ai._clock
    for adviser in ai.engines.values():
        adviser._clock = ai._clock
    return NativeGrokReview(ai, service, bridge, clock=lambda: clock[0]), ai


def request(review, engine=EngineId.WARRIOR, **changes):
    v = value(engine)
    return review.prepare(v, 10, 1, authorization=grant(v, **changes), evidence={"setup": "TRIGGERED"},
        float_provenance=FloatProvenance.UNKNOWN, symbol_authorization=AUTH)


def submit(review, r):
    return review.submit([r], engine_evidence={}, account_evidence={})


def finished(review):
    review.handoff.worker._thread.join(2)
    assert not review.handoff.worker._thread.is_alive()


@pytest.mark.parametrize("engine", [EngineId.WARRIOR, EngineId.SCALPER])
def test_native_review_returns_pending_and_adopts_only_on_owner_lane(setup, warrior, engine):
    commands, *_ = setup
    entered, release = Event(), Event()
    def block(context, answer):
        entered.set()
        assert release.wait(2)
    review, ai = create(setup, block)
    try:
        r = request(review, engine)
        receipt = submit(review, r)
        assert receipt.pending and receipt.state == "PENDING_REVIEW"
        assert entered.wait(1)
        assert not commands.order_book.history()
        assert review.pending_states() == {receipt.candidate_ids[0]: "PENDING_REVIEW"}
        assert submit(review, r).reason == "BUSY"
        release.set()
        finished(review)
        assert not warrior.open_paper_symbols
        assert review.drain(warrior, scalper(setup)) == receipt.candidate_ids
        assert submit(review, r).reason == "BUSY"  # unread resolution retains producer ownership
        resolutions = review.take_resolutions()
        assert len(resolutions) == 1 and resolutions[0].state == "ACKNOWLEDGED"
        assert resolutions[0].result.success
        assert resolutions[0].entry.prepared.placement is r.entry.prepared.placement
        assert not review.take_resolutions() and not review.pending_states()
        assert len([o for o in commands.order_book.history() if o.request.side.value == "BUY"]) == 1
        assert ai.client.requests == 2
    finally:
        release.set()
        review.request_stop()


@pytest.mark.parametrize("field", ["setup_confirmed", "tradable", "halt_clear", "session_allowed", "execution_permitted"])
def test_each_native_hard_gate_revokes_during_review(setup, field):
    review = None
    r = None
    def revoke(context, answer):
        if context["role"] == "MASTER":
            assert review.refresh(r.entry.prepared.candidate.candidate_id, replace(r.authorization, **{field: False}))
    review, ai = create(setup, revoke)
    r = request(review)
    assert submit(review, r).pending
    finished(review)
    assert not setup[0].order_book.history()
    review.drain(None, None)
    resolutions = review.take_resolutions()
    assert resolutions[0].state == "NO_ORDER"
    assert resolutions[0].reason == "AUTHORIZATION_REVOKED"
    assert not review.pending_states()
    assert ai.client.requests == 2
    review.request_stop()


@pytest.mark.parametrize("delta", [timedelta(seconds=3)])
def test_stale_or_future_authorization_never_dispatches(setup, delta):
    def advance(context, answer):
        if context["role"] == "MASTER":
            setup[-1][0] = NOW + delta
    review, _ = create(setup, advance)
    assert submit(review, request(review)).pending
    finished(review)
    review.drain(None, None)
    assert review.take_resolutions()[0].reason == "AUTHORIZATION_REVOKED"
    assert not setup[0].order_book.history()
    review.request_stop()


def test_fresh_refresh_allows_review_without_extending_candidate_deadline(setup):
    r = review = None
    def update(context, answer):
        if context["role"] == "MASTER":
            setup[-1][0] = NOW + timedelta(seconds=3)
            setup[-2][0] = replace(setup[-2][0], bid_timestamp=setup[-1][0], ask_timestamp=setup[-1][0], last_timestamp=setup[-1][0])
            refreshed = replace(r.authorization, observed_at=setup[-1][0])
            assert review.refresh(r.entry.prepared.candidate.candidate_id, refreshed)
    review, _ = create(setup, update)
    r = request(review)
    assert submit(review, r).pending
    finished(review)
    calls = []
    runtime = SimpleNamespace(adopt_acknowledged_entry=lambda *a, **kw: calls.append(get_ident()) or True)
    review.drain(runtime, None)
    assert calls == [get_ident()]
    assert review.take_resolutions()[0].state == "ACKNOWLEDGED"
    review.request_stop()


@pytest.mark.parametrize("change", ["lifecycle", "extension", "older", "future", "unknown"])
def test_refresh_cannot_rebind_extend_or_use_noncurrent_evidence(setup, change):
    entered, release = Event(), Event()
    def block(context, answer):
        entered.set()
        assert release.wait(2)
    review, _ = create(setup, block)
    r = request(review)
    try:
        assert submit(review, r).pending and entered.wait(1)
        key = r.entry.prepared.candidate.candidate_id
        auth = r.authorization
        if change == "lifecycle":
            auth = replace(auth, lifecycle_id="other")
        elif change == "extension":
            auth = replace(auth, observed_at=NOW + timedelta(seconds=1), valid_until=NOW + timedelta(seconds=61))
        elif change == "older":
            auth = replace(auth, observed_at=NOW - timedelta(seconds=1), valid_until=NOW + timedelta(seconds=59))
        elif change == "future":
            auth = replace(auth, observed_at=NOW + timedelta(seconds=1))
        else:
            key = "unknown"
        assert not review.refresh(key, auth)
    finally:
        review.request_stop()
        release.set()
        finished(review)
        review.drain(None, None)
        assert review.take_resolutions()[0].state == "NO_ORDER"


@pytest.mark.parametrize("role", [EngineId.WARRIOR.value, "MASTER"])
def test_abstention_is_no_order_and_resolution_can_be_consumed_once(setup, role):
    def abstain(context, answer):
        if context["role"] == role:
            answer["selected_ids"] = []
    review, _ = create(setup, abstain)
    r = request(review)
    assert submit(review, r).pending
    finished(review)
    review.drain(None, None)
    assert review.take_resolutions()[0].state == "NO_ORDER"
    assert not review.take_resolutions()
    assert not setup[0].order_book.history()
    review.request_stop()


def test_uncertain_dispatch_retains_pending_and_blocks_retry(setup):
    review, _ = create(setup)
    r = request(review)
    key = r.entry.prepared.candidate.candidate_id
    # Inject a transport failure at the controlled dispatch boundary.
    review.handoff.worker.dispatcher.service.place_order = lambda _: (_ for _ in ()).throw(RuntimeError("uncertain"))
    assert submit(review, r).pending
    finished(review)
    review.drain(None, None)
    assert review.pending_states()[key] == "RECONCILIATION_REQUIRED"
    assert not review.take_resolutions()
    assert submit(review, r).reason == "BUSY"
    review.request_stop()


def test_acknowledgement_pending_is_retained_without_rebuy(setup, warrior):
    review, _ = create(setup)
    r = request(review)
    assert submit(review, r).pending
    finished(review)
    review.drain(None, None)
    assert review.pending_states()[r.entry.prepared.candidate.candidate_id] == "ACKNOWLEDGEMENT_PENDING"
    assert not review.take_resolutions()
    assert submit(review, r).reason == "BUSY"
    review.drain(warrior, None)
    assert review.take_resolutions()[0].state == "ACKNOWLEDGED"
    assert len(setup[0].order_book.history()) == 1
    review.request_stop()


def test_closed_canonical_order_does_not_install_runtime_state(setup, warrior):
    review, _ = create(setup)
    assert submit(review, request(review)).pending
    finished(review)
    order = setup[0].order_book.history()[0]
    setup[0].order_book.cancel(order.order_id)
    review.drain(warrior, None)
    assert review.take_resolutions()[0].state == "POSITION_CLOSED"
    assert not warrior.open_paper_symbols
    review.request_stop()


@pytest.mark.parametrize("change", ["false", "stale", "lifecycle"])
def test_invalid_native_authorization_is_rejected_before_model_request(setup, change):
    review, ai = create(setup)
    auth = grant(signal())
    if change == "false":
        auth = replace(auth, setup_confirmed=False)
    elif change == "stale":
        setup[-1][0] = NOW + timedelta(seconds=3)
    else:
        auth = replace(auth, lifecycle_id="other")
    with pytest.raises(ValueError):
        review.prepare(signal(), 10, 1, authorization=auth, evidence={},
            float_provenance=FloatProvenance.UNKNOWN, symbol_authorization=AUTH)
    assert ai.client.requests == 0 and not setup[0].order_book.history()
    review.request_stop()


@pytest.mark.parametrize("change", [{"halt_clear": 1}, {"observed_at": NOW.replace(tzinfo=None)},
    {"valid_until": NOW + timedelta(seconds=61)}, {"valid_until": NOW}])
def test_authorization_requires_typed_flags_and_bounded_aware_times(change):
    with pytest.raises(ValueError):
        grant(signal(), **change)


def test_pending_receipt_cannot_be_used_as_success_boolean(setup):
    review, _ = create(setup)
    receipt = submit(review, request(review))
    with pytest.raises(TypeError, match="explicit"):
        bool(receipt)
    finished(review)
    review.drain(None, None)
    review.request_stop()


def test_stop_during_blocked_review_retains_pending_until_owner_consumes_receipt(setup):
    entered, release = Event(), Event()
    def block(context, answer):
        entered.set()
        assert release.wait(2)
    review, _ = create(setup, block)
    r = request(review)
    try:
        assert submit(review, r).pending and entered.wait(1)
        assert not review.close(None, None)
        assert not review.refresh(r.entry.prepared.candidate.candidate_id, r.authorization)
        assert submit(review, r).reason == "CLOSED"
        with pytest.raises(ValueError, match="closed"):
            request(review)
        release.set()
        finished(review)
        assert not review.close(None, None)  # completion is not consumed by close
        resolution = review.take_resolutions()
        assert resolution[0].state == "NO_ORDER"
        assert review.close(None, None)
        assert not setup[0].order_book.history()
    finally:
        release.set()
        review.request_stop()


@pytest.mark.parametrize("change", ["empty", "duplicate", "too_many", "bad_evidence", "broken_iterable"])
def test_invalid_batches_do_not_leave_pending_state_or_call_ai(setup, change):
    review, ai = create(setup)
    r = request(review)
    batch = [r]
    evidence = {}
    if change == "empty":
        batch = []
    elif change == "duplicate":
        batch = [r, r]
    elif change == "too_many":
        batch = [r] * 33
    elif change == "bad_evidence":
        evidence = {"bad": float("nan")}
    else:
        def broken():
            raise RuntimeError("broken producer")
            yield r
        batch = broken()
    receipt = review.submit(batch, engine_evidence=evidence, account_evidence={})
    assert not receipt.pending and receipt.reason == "INVALID"
    assert not review.pending_states() and not review._entries and not review._grants
    assert ai.client.requests == 0
    review.request_stop()


def test_pending_review_rejects_future_grant_before_worker_start(setup):
    review, ai = create(setup)
    r = request(review)
    future = replace(r.authorization, observed_at=NOW + timedelta(milliseconds=1))
    r = replace(r, authorization=future)
    assert submit(review, r).reason == "AUTHORIZATION_NOT_CURRENT"
    assert ai.client.requests == 0 and not review.pending_states()
    review.request_stop()

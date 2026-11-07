"""Native desktop producer leases, callback binding and owner-lane shutdown."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from threading import Event

import pytest

from app.asset_modules.grok_desktop_producer import DesktopGrokProducer
from app.asset_modules.grok_native_review import NativeGrokReview
from app.asset_modules.grok_paper_entries import GrokPaperEntries
from app.asset_modules.engine_catalog import EngineId
from app.strategies.warrior_momentum import WarriorDesktopSidecar
from tests.asset_modules.test_controlled_paper_service import setup, NOW
from tests.asset_modules.test_grok_paper_entries import prepared, suite, run
from tests.asset_modules.test_grok_runtime_handoff import warrior, signal, AUTH, FloatProvenance, scalper
from tests.asset_modules.test_grok_native_review import create, finished
from tests.asset_modules.test_native_review_branches import arm


def producer(setup, transform=None):
    review, ai = create(setup, transform)
    permission = [True]
    return DesktopGrokProducer(review, permitted=lambda: permission[0]), ai, permission


def offer(p, v=None):
    return p.warrior_offer(v or signal(), 10, D(1), float_provenance=FloatProvenance.UNKNOWN,
        symbol_authorization=AUTH, evaluated_at=NOW)


def test_model_evidence_uses_only_retained_completed_symbol_bars(setup, monkeypatch):
    from app.strategies.warrior_momentum.models import MinuteBar
    from app.asset_modules.grok_native_review import NativeReviewReceipt
    p, ai, _ = producer(setup)
    symbol = signal().symbol
    completed = MinuteBar(symbol, NOW-timedelta(minutes=1), D(6), D(6), D(6), D(6), D(10))
    future = replace(completed, timestamp=NOW)
    other = replace(completed, symbol='OTHER')
    p.history_source = lambda s, at: (completed, future, other)
    received = []
    def submit(requests, **evidence):
        received.append(evidence['engine_evidence'][EngineId.WARRIOR.value])
        return NativeReviewReceipt('NOT_SENT', 'FIXTURE')
    monkeypatch.setattr(p.review, 'submit', submit)
    try:
        assert offer(p).state == 'NOT_SENT'
        assert received[0]['history_status'] == 'RETAINED_COMPLETED_BARS'
        assert len(received[0]['completed_native_bars']) == 1
        assert received[0]['completed_native_bars'][0]['symbol'] == symbol
        assert ai.client.requests == 0
    finally:p.request_stop()


def desktop(setup, tmp_path, p):
    commands, bridge, _, account, quote, _ = setup
    observer = scalper(setup)
    sidecar = WarriorDesktopSidecar(enabled=True, environment='PAPER',
        storage_path=tmp_path / 'desktop.db', clock=lambda: NOW,
        account_context_source=lambda: account[0], paper_entry_submitter=bridge.submit_entry_decision,
        paper_exit_submitter=bridge.ensure_exit, paper_order_source=commands.order_book.get,
        paper_acknowledgement_adopter=bridge.adopt_entry_acknowledgement,
        paper_position_quantity_source=bridge._authoritative_quantity,
        execution_quote_source=lambda s: quote[0], quick_scalper_observer=observer,
        strategy_ownership=observer.ownership)
    sidecar.bind_grok_native_producer(p)
    return sidecar, observer


@pytest.mark.parametrize('change', ['stale', 'expired', 'future_signal', 'future_evaluation', 'permission'])
def test_invalid_offer_does_not_start_model_or_extend_signal_expiry(setup, change):
    p, ai, permission = producer(setup)
    v = signal()
    if change == 'stale':
        setup[-1][0] = NOW + timedelta(seconds=3)
    elif change == 'expired':
        v = replace(v, timestamp=NOW - timedelta(seconds=60))
    elif change == 'future_signal':
        v = replace(v, timestamp=NOW + timedelta(milliseconds=1))
    elif change == 'future_evaluation':
        setup[-1][0] = NOW - timedelta(milliseconds=1)
    else:
        permission[0] = False
    receipt = offer(p, v)
    assert not receipt.pending and ai.client.requests == 0
    assert not setup[0].order_book.history()
    p.request_stop()


def test_permission_is_rechecked_after_master_without_waiting_for_another_frame(setup):
    permission = None
    def revoke(c, a):
        if c['role'] == 'MASTER':
            permission[0] = False
    p, ai, permission = producer(setup, revoke)
    assert offer(p).pending
    finished(p.review)
    p.review.drain(None, None)
    assert p.review.take_resolutions()[0].reason == 'AUTHORIZATION_REVOKED'
    assert not setup[0].order_book.history()
    p.request_stop()


@pytest.mark.parametrize('change', ['stop', 'trigger', 'lifecycle', 'permission'])
def test_current_warrior_refresh_revokes_changed_structure(setup, change):
    entered, release = Event(), Event()
    def block(c, a):
        entered.set()
        assert release.wait(2)
    p, _, permission = producer(setup, block)
    try:
        assert offer(p).pending and entered.wait(1)
        v = signal()
        if change == 'stop':
            v = replace(v, stop_price=D('5.8'))
        elif change == 'trigger':
            v = replace(v, entry_trigger=D('6.01'))
        elif change == 'lifecycle':
            v = replace(v, structural_episode_id='other')
        else:
            permission[0] = False
        assert p.warrior_refresh(v, evaluated_at=NOW)
        release.set()
        finished(p.review)
        p.review.drain(None, None)
        assert p.review.take_resolutions()[0].reason == 'AUTHORIZATION_REVOKED'
        assert not setup[0].order_book.history()
    finally:
        release.set()
        p.request_stop()


def test_native_refresh_preserves_original_deadline(setup):
    entered, release = Event(), Event()
    def block(c, a):
        entered.set()
        assert release.wait(2)
    p, _, _ = producer(setup, block)
    try:
        v = replace(signal(), timestamp=NOW - timedelta(seconds=30))
        receipt = offer(p, v)
        assert receipt.pending and entered.wait(1)
        key = receipt.candidate_ids[0]
        assert p.review._grants[key].valid_until == NOW + timedelta(seconds=30)
        setup[-1][0] = NOW + timedelta(seconds=1)
        assert p.warrior_refresh(v, evaluated_at=setup[-1][0])
        assert p.review._grants[key].valid_until == NOW + timedelta(seconds=30)
    finally:
        p.request_stop()
        release.set()
        finished(p.review)


def test_alternating_engines_do_not_leak_prior_candidates_into_master(setup):
    commands, _, service, _, _, clock = setup
    ai, ticks = suite()
    dispatch = GrokPaperEntries(ai, service, authorize=lambda _: True, clock=lambda: clock[0])
    assert run(dispatch, [prepared(commands)]).outcomes[0].state == 'SUBMITTED'
    ticks[0] += 60  # Advance the API cooldown only; candidate wall time stays current.
    report = run(dispatch, [prepared(commands, EngineId.SCALPER, 'QQQ')])
    assert report.outcomes and report.outcomes[0].state == 'SUBMITTED', report
    assert ai.client.requests == 4
    assert len(commands.order_book.history()) == 2


def test_desktop_binding_and_start_wire_callbacks_without_model_requests(setup, tmp_path):
    p, ai, _ = producer(setup)
    sidecar, observer = desktop(setup, tmp_path, p)
    assert not p._permitted() and ai.client.requests == 0
    sidecar.start('PAPER')
    try:
        assert p._permitted()
        assert sidecar._service._paper_entry_reviewer == p.warrior_offer
        assert sidecar._service._paper_entry_review_refresher == p.warrior_refresh
        assert observer._paper_entry_reviewer == p.scalper_offer
        assert observer.scalper._review_refresher == p.scalper_refresh
        assert not sidecar.poll_grok_completions()
        assert ai.client.requests == 0
        with pytest.raises(ValueError, match='stopped'):
            sidecar.bind_grok_native_producer(p)
    finally:
        sidecar.stop()
    assert not p._permitted() and p.review._closed


def test_desktop_poll_resolves_native_bookkeeping_on_owner_lane(setup, tmp_path):
    p, ai, _ = producer(setup)
    sidecar, observer = desktop(setup, tmp_path, p)
    sidecar.start('PAPER')
    try:
        service = sidecar._service
        v = signal()
        arm(service, v)
        service._open_paper(v, 10, D(1), FloatProvenance.UNKNOWN, AUTH, review_timestamp=NOW)
        finished(p.review)
        assert not service.open_paper_symbols
        applied = sidecar.poll_grok_completions()
        assert len(applied) == 1 and service.open_paper_symbols == ('SPY',)
        assert not p._requests and not p.review.pending_states()
        assert not sidecar.poll_grok_completions()
        assert ai.client.requests == 2 and len(setup[0].order_book.history()) == 1
    finally:
        sidecar.stop()


def test_desktop_stop_keeps_runtime_until_blocked_review_resolves(setup, tmp_path):
    entered, release = Event(), Event()
    def block(c, a):
        entered.set()
        assert release.wait(2)
    p, _, _ = producer(setup, block)
    sidecar, observer = desktop(setup, tmp_path, p)
    sidecar.start('PAPER')
    try:
        v = signal()
        arm(sidecar._service, v)
        sidecar._service._open_paper(v, 10, D(1), FloatProvenance.UNKNOWN, AUTH, review_timestamp=NOW)
        assert entered.wait(1)
        original = sidecar._service
        with pytest.raises(TimeoutError, match='Grok entry'):
            sidecar.stop()
        assert sidecar._service is original and not p._permitted()
        release.set()
        finished(p.review)
        sidecar.stop()
        assert not setup[0].order_book.history() and not p._requests
    finally:
        release.set()
        finished(p.review)
        sidecar.stop()

@pytest.mark.parametrize('change', ['none', 'stop', 'generation', 'wait'])
def test_scalper_producer_refresh_uses_current_native_assessment(setup, change):
    from tests.warrior_momentum.test_quick_scalper_runtime import snapshot as runtime_snapshot
    from app.strategies.warrior_momentum.quick_scalper import ScalpDecision
    entered, release = Event(), Event()
    def block(c, a):
        entered.set()
        assert release.wait(2)
    p, _, _ = producer(setup, block)
    runtime = scalper(setup)
    runtime.bind_native_review_callbacks(p.scalper_offer, p.scalper_refresh)
    s = replace(runtime_snapshot(), symbol='SPY', last=D(6), bid=D('5.99'), ask=D(6),
        structural_stop=D('5.9'), observed_at=NOW, decision_at=NOW,
        last_timestamp=NOW, bid_timestamp=NOW, ask_timestamp=NOW)
    runtime._snapshots[(s.symbol, s.generation_id)] = s
    runtime._current_generation[s.symbol] = s.generation_id
    try:
        assessed = runtime.scalper.observe(s)
        assert entered.wait(1) and len(p._requests) == 1
        if change == 'stop':
            s = replace(s, structural_stop=D('5.8'))
        elif change == 'generation':
            s = replace(s, generation_id='other')
        elif change == 'wait':
            assessed = replace(assessed, decision=ScalpDecision.WAIT)
        assert p.scalper_refresh(s, assessed)
        release.set()
        finished(p.review)
        p.review.drain(None, runtime)
        resolutions = p.review.take_resolutions()
        assert len(resolutions) == 1
        assert (resolutions[0].reason == 'AUTHORIZATION_REVOKED') == (change != 'none')
        assert bool(setup[0].order_book.history()) == (change == 'none')
    finally:
        release.set()
        p.request_stop()

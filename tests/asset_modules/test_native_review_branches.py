"""Native entry branches keep review ownership separate from broker orders."""
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_native_review import NativeReviewReceipt
from app.asset_modules.grok_runtime_handoff import ReviewResolution
from app.strategies.warrior_momentum.autonomous_paper import lifecycle_identity
from app.strategies.warrior_momentum.opportunity_engine import WarriorOpportunityEngine, WarriorOpportunityState, AdaptiveOpportunityResult
from app.strategies.warrior_momentum.quick_scalper import QuickScalper, QuickScalperConfig, SymbolOwnershipRegistry, StrategyOwner, CanonicalScalpSubmission
from app.strategies.warrior_momentum import ForwardCaptureStore, ForwardCaptureWriter, WarriorForwardCaptureService
from tests.asset_modules.test_controlled_paper_service import setup, NOW
from tests.asset_modules.test_grok_runtime_handoff import warrior, signal, AUTH, FloatProvenance, scalper
from tests.asset_modules.test_grok_native_review import create, grant, finished
from tests.warrior_momentum.test_opportunity_engine import assessment
from tests.warrior_momentum.test_quick_scalper import snapshot
from tests.warrior_momentum.test_forward_capture import point, account


def arm(runtime, v):
    generation = lifecycle_identity(v)
    runtime.opportunity_engine.apply_assessment(replace(assessment(), symbol=v.symbol,
        generation_id=generation, lifecycle_id=generation,
        adaptive_result=AdaptiveOpportunityResult.EXECUTABLE))
    runtime.strategy_ownership = SymbolOwnershipRegistry()
    assert runtime.strategy_ownership.acquire(v.symbol, StrategyOwner.WARRIOR_MOMENTUM, generation)


def test_warrior_evaluation_pending_does_not_release_ownership_or_report_rejection(tmp_path):
    store = ForwardCaptureStore(tmp_path / 'capture.db')
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    ownership = SymbolOwnershipRegistry()
    calls = []
    def review(*args, **kwargs):
        calls.append((args, kwargs))
        return NativeReviewReceipt('PENDING_REVIEW', 'AI_REVIEW_PENDING', ('candidate',))
    runtime = WarriorForwardCaptureService(store, writer, strategy_ownership=ownership,
        paper_entry_submitter=lambda *a: pytest.fail('Synchronous buy during review'),
        paper_entry_reviewer=review)
    try:
        p = point()
        candidate, entry = runtime.observe(p, account=account())
        assert len(calls) == 1
        assert entry is None and not runtime.open_paper_symbols
        v = calls[0][0][0]
        generation = lifecycle_identity(v)
        assert ownership.owner(v.symbol) == (StrategyOwner.WARRIOR_MOMENTUM, generation)
        item = runtime.opportunity_engine.get(v.symbol, generation)
        assert item.state is WarriorOpportunityState.REVIEW_PENDING
        assert item.authorization_result == 'PENDING_REVIEW' and item.order_intent_result is None
        runtime.observe(p, account=account())
        assert len(calls) == 1
        assert ownership.owner(v.symbol)
    finally:
        writer.close()


@pytest.mark.parametrize('engine', [EngineId.WARRIOR, EngineId.SCALPER])
def test_opportunity_review_cannot_be_downgraded_by_quotes(engine):
    book = WarriorOpportunityEngine()
    a = replace(assessment(), strategy='QUICK_SCALPER' if engine is EngineId.SCALPER else 'WARRIOR_MOMENTUM',
        adaptive_result=AdaptiveOpportunityResult.EXECUTABLE)
    book.apply_assessment(a)
    item = book.mark_review_pending(a.symbol, a.generation_id, at=a.decision_timestamp)
    assert item.state is WarriorOpportunityState.REVIEW_PENDING
    assert book.apply_assessment(a).state is WarriorOpportunityState.REVIEW_PENDING
    assert book.mark_order_intent(a.symbol, a.generation_id, at=a.decision_timestamp) is None
    assert book.release_review(a.symbol, a.generation_id, at=a.decision_timestamp).state is WarriorOpportunityState.WAITING_EXECUTION


def test_scalper_pending_keeps_claim_does_not_count_attempt_and_refreshes_without_buy():
    owner = SymbolOwnershipRegistry()
    book = WarriorOpportunityEngine()
    calls, refreshes = [], []
    def review(a):
        calls.append(a)
        return CanonicalScalpSubmission(False, True, False, False, 'AI_REVIEW_PENDING',
            a.opportunity.lifecycle_id, review_pending=True, review_candidate_id='candidate')
    engine = QuickScalper(engine=book, ownership=owner, config=QuickScalperConfig(enabled=True),
        authorize_and_submit=review, review_refresher=lambda *a: refreshes.append(a))
    s = snapshot()
    engine.observe(s)
    assert len(calls) == 1
    assert owner.owner(s.symbol) == (StrategyOwner.QUICK_SCALPER, calls[0].opportunity.lifecycle_id)
    assert not engine.guard._generations and not any(engine.guard._attempts.values())
    assert book.get(s.symbol).state is WarriorOpportunityState.REVIEW_PENDING
    engine.observe(s)
    assert len(calls) == 1 and len(refreshes) == 1
    assert owner.owner(s.symbol)


@pytest.mark.parametrize('outcome', ['ack', 'no_order', 'closed', 'failed_adoption', 'callback_exception'])
def test_warrior_native_branch_consumes_bound_canonical_receipt_once(setup, warrior, outcome):
    review, _ = create(setup, (lambda c, a: a.update(selected_ids=[])) if outcome == 'no_order' else None)
    v = signal()
    arm(warrior, v)
    requests = []
    def offer(v, shares, risk, **context):
        r = review.prepare(v, shares, risk, authorization=grant(v), evidence={},
            float_provenance=context['float_provenance'], symbol_authorization=context['symbol_authorization'])
        requests.append(r)
        receipt = review.submit([r], engine_evidence={}, account_evidence={})
        if outcome == 'callback_exception':
            raise RuntimeError('Failed after handing off')
        return receipt
    warrior._paper_entry_reviewer = offer
    records, _, decision = warrior._open_paper(v, 10, D(1), FloatProvenance.UNKNOWN, AUTH, review_timestamp=NOW)
    assert not records and not decision.authorized and not decision.submission_attempted
    warrior.opportunity_engine.mark_review_pending(v.symbol, lifecycle_identity(v), at=NOW)
    finished(review)
    if outcome == 'closed':
        setup[0].order_book.cancel(setup[0].order_book.history()[0].order_id)
    review.drain(warrior if outcome != 'failed_adoption' else None, None)
    if outcome == 'failed_adoption':
        assert not review.take_resolutions()
        assert warrior.strategy_ownership.owner(v.symbol)
        review.drain(warrior, None)
    receipt = review.take_resolutions()[0]
    assert warrior.resolve_native_review(receipt, at=NOW)
    assert not warrior.resolve_native_review(receipt, at=NOW)
    item = warrior.opportunity_engine.get(v.symbol)
    if outcome in {'no_order', 'closed'}:
        assert warrior.strategy_ownership.owner(v.symbol) is None
        assert not warrior.open_paper_symbols
        assert item.state in {WarriorOpportunityState.WAITING_EXECUTION, WarriorOpportunityState.COMPLETED}
    else:
        assert warrior.strategy_ownership.owner(v.symbol)
        assert item.state is WarriorOpportunityState.ORDER_PENDING and item.order_intent_result == 'SUBMITTED'
        assert len([o for o in setup[0].order_book.history() if o.request.side.value == 'BUY']) == 1
    review.request_stop()


def test_warrior_bad_reviewer_remains_pending_and_never_falls_back(setup, warrior):
    v = signal()
    arm(warrior, v)
    warrior._paper_entry_reviewer = lambda *a, **kw: None
    _, _, decision = warrior._open_paper(v, 10, D(1), FloatProvenance.UNKNOWN, AUTH)
    assert decision.reason.value == 'AI_REVIEW_RECONCILIATION_REQUIRED'
    assert warrior.strategy_ownership.owner(v.symbol)
    assert not setup[0].order_book.history()


def test_no_order_receipt_cannot_disguise_successful_order(setup, warrior):
    review, _ = create(setup)
    v = signal()
    arm(warrior, v)
    def offer(*args, **kwargs):
        r = review.prepare(v, 10, D(1), authorization=grant(v), evidence={},
            float_provenance=FloatProvenance.UNKNOWN, symbol_authorization=AUTH)
        return review.submit([r], engine_evidence={}, account_evidence={})
    warrior._paper_entry_reviewer = offer
    warrior._open_paper(v, 10, D(1), FloatProvenance.UNKNOWN, AUTH)
    finished(review)
    review.drain(warrior, None)
    resolution = review.take_resolutions()[0]
    assert not warrior.resolve_native_review(replace(resolution, state='NO_ORDER'), at=NOW)
    assert warrior.strategy_ownership.owner(v.symbol)
    assert warrior.resolve_native_review(resolution, at=NOW)
    review.request_stop()


@pytest.mark.parametrize('outcome', ['ack', 'no_order', 'closed', 'exception', 'malformed', 'controller_refusal'])
def test_scalper_runtime_entry_branch_preserves_review_ownership_and_resolves(setup, outcome):
    from tests.warrior_momentum.test_quick_scalper_runtime import snapshot as runtime_snapshot
    runtime = scalper(setup)
    def change(context, answer):
        if outcome == 'no_order':
            answer['selected_ids'] = []
        elif outcome == 'controller_refusal' and context['role'] == 'MASTER':
            setup[4][0] = None
    review, _ = create(setup, change)
    s = replace(runtime_snapshot(), symbol='SPY', last=D(6), bid=D('5.99'), ask=D(6),
        structural_stop=D('5.9'), observed_at=NOW, decision_at=NOW,
        last_timestamp=NOW, bid_timestamp=NOW, ask_timestamp=NOW)
    runtime._snapshots[(s.symbol, s.generation_id)] = s
    runtime._current_generation[s.symbol] = s.generation_id
    called = []
    def offer(intent, shares, risk):
        called.append(intent)
        if outcome == 'malformed':
            return None
        r = review.prepare(intent, shares, risk, authorization=grant(intent), evidence={})
        receipt = review.submit([r], engine_evidence={}, account_evidence={})
        if outcome == 'exception':
            raise RuntimeError('Failure after actual offer')
        return receipt
    runtime._paper_entry_reviewer = offer
    runtime.scalper.observe(s)
    assert len(called) == 1
    assert runtime.ownership.owner('SPY')
    assert not runtime.scalper.guard._generations
    assert runtime.engine.get('SPY').state is WarriorOpportunityState.REVIEW_PENDING
    runtime.scalper.observe(s)
    assert len(called) == 1  # Never another buy even if assessment state changes.
    if outcome == 'malformed':
        assert runtime.scalper._pending_reviews
        assert not setup[0].order_book.history()
        review.request_stop()
        return
    finished(review)
    if outcome == 'closed':
        setup[0].order_book.cancel(setup[0].order_book.history()[0].order_id)
    review.drain(None, runtime)
    resolutions = review.take_resolutions()
    assert len(resolutions) == 1
    resolution = resolutions[0]
    assert runtime.resolve_native_review(resolution, at=NOW)
    assert not runtime.resolve_native_review(resolution, at=NOW)
    if outcome in {'no_order', 'closed', 'controller_refusal'}:
        assert not runtime.ownership.owner('SPY')
        if outcome in {'no_order', 'controller_refusal'}:
            assert not runtime.scalper.guard._generations
    else:
        assert runtime.ownership.owner('SPY')
        assert runtime.scalper.guard._generations
        assert len([o for o in setup[0].order_book.history() if o.request.side.value == 'BUY']) == 1
    review.request_stop()


def test_completion_pump_retries_bookkeeping_only_and_blocks_another_review(setup, warrior):
    review, _ = create(setup)
    v = signal()
    arm(warrior, v)
    requests = []
    def offer(*args, **context):
        r = review.prepare(v, 10, D(1), authorization=grant(v), evidence={},
            float_provenance=FloatProvenance.UNKNOWN, symbol_authorization=AUTH)
        requests.append(r)
        return review.submit([r], engine_evidence={}, account_evidence={})
    warrior._paper_entry_reviewer = offer
    warrior._open_paper(v, 10, D(1), FloatProvenance.UNKNOWN, AUTH)
    finished(review)
    resolver = warrior.resolve_native_review
    warrior.resolve_native_review = lambda *a, **kw: False
    assert not review.apply_resolutions(warrior, None, at=NOW)
    key = requests[0].entry.prepared.candidate.candidate_id
    assert review.pending_states()[key] == 'NATIVE_RECEIPT_PENDING'
    assert review.submit(requests, engine_evidence={}, account_evidence={}).reason == 'BUSY'
    warrior.resolve_native_review = resolver
    assert review.apply_resolutions(warrior, None, at=NOW) == (key,)
    assert not review.apply_resolutions(warrior, None, at=NOW)
    assert not review.pending_states()
    assert len(setup[0].order_book.history()) == 1
    review.request_stop()


@pytest.mark.parametrize('state', [WarriorOpportunityState.ENTERED,
    WarriorOpportunityState.PARTIALLY_FILLED, WarriorOpportunityState.PROTECTED,
    WarriorOpportunityState.MANAGING, WarriorOpportunityState.EXIT_REQUESTED,
    WarriorOpportunityState.COMPLETED])
def test_ack_receipt_does_not_regress_advanced_native_position_state(state):
    book = WarriorOpportunityEngine()
    a = replace(assessment(), adaptive_result=AdaptiveOpportunityResult.EXECUTABLE)
    book.apply_assessment(a)
    book._items[(a.symbol, a.generation_id)] = replace(book.get(a.symbol), state=state)
    item = book.mark_review_acknowledged(a.symbol, a.generation_id, at=a.decision_timestamp)
    assert item.state is state
    assert item.authorization_result == 'AUTHORIZED' and item.order_intent_result == 'SUBMITTED'


def test_uncertain_frontend_callback_cannot_be_released_by_no_order_receipt(setup, warrior):
    review, _ = create(setup, lambda c, a: a.update(selected_ids=[]))
    v = signal()
    arm(warrior, v)
    def offer(*args, **kw):
        r = review.prepare(v, 10, D(1), authorization=grant(v), evidence={},
            float_provenance=FloatProvenance.UNKNOWN, symbol_authorization=AUTH)
        review.submit([r], engine_evidence={}, account_evidence={})
        raise RuntimeError('No bound receipt returned')
    warrior._paper_entry_reviewer = offer
    warrior._open_paper(v, 10, D(1), FloatProvenance.UNKNOWN, AUTH)
    finished(review)
    assert not review.apply_resolutions(warrior, None, at=NOW)
    assert warrior.strategy_ownership.owner(v.symbol)
    assert warrior._pending_native_reviews
    review.request_stop()


def test_cancelled_unfilled_scalper_review_does_not_reset_loss_streak(setup):
    from tests.warrior_momentum.test_quick_scalper_runtime import snapshot as runtime_snapshot
    runtime = scalper(setup)
    runtime.scalper.guard._consecutive_losses = 2
    review, _ = create(setup)
    s = replace(runtime_snapshot(), symbol='SPY', last=D(6), bid=D('5.99'), ask=D(6),
        structural_stop=D('5.9'), observed_at=NOW, decision_at=NOW,
        last_timestamp=NOW, bid_timestamp=NOW, ask_timestamp=NOW)
    runtime._snapshots[(s.symbol, s.generation_id)] = s
    def offer(intent, shares, risk):
        r = review.prepare(intent, shares, risk, authorization=grant(intent), evidence={})
        return review.submit([r], engine_evidence={}, account_evidence={})
    runtime._paper_entry_reviewer = offer
    runtime.scalper.observe(s)
    finished(review)
    setup[0].order_book.cancel(setup[0].order_book.history()[0].order_id)
    assert review.apply_resolutions(None, runtime, at=NOW)
    assert runtime.scalper.guard._consecutive_losses == 2
    assert not runtime.scalper.guard._open
    review.request_stop()


def test_pending_generation_change_does_not_start_second_scalper_review():
    owner = SymbolOwnershipRegistry()
    calls = []
    def offer(a):
        calls.append(a)
        return CanonicalScalpSubmission(False, True, False, False, 'AI_REVIEW_PENDING',
            a.opportunity.lifecycle_id, review_pending=True, review_candidate_id='candidate')
    engine = QuickScalper(engine=WarriorOpportunityEngine(), ownership=owner,
        config=QuickScalperConfig(enabled=True), authorize_and_submit=offer)
    engine.observe(snapshot())
    before = owner.owner('FAST')
    engine.observe(snapshot(generation='g2'))
    assert len(calls) == 1 and owner.owner('FAST') == before


def test_busy_warrior_review_waits_without_terminal_rejection_and_retries(tmp_path):
    store = ForwardCaptureStore(tmp_path / 'busy.db')
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    ownership = SymbolOwnershipRegistry()
    calls = []
    def reviewer(*args, **kw):
        calls.append(args[0])
        return NativeReviewReceipt('NOT_SENT', 'BUSY') if len(calls) == 1 else NativeReviewReceipt('PENDING_REVIEW', 'AI_REVIEW_PENDING', ('candidate',))
    runtime = WarriorForwardCaptureService(store, writer, strategy_ownership=ownership,
        paper_entry_reviewer=reviewer, paper_entry_submitter=lambda *a: pytest.fail('Fallback buy'))
    try:
        _, entry = runtime.observe(point(), account=account())
        assert entry is None and len(calls) == 1
        assert ownership.owner(calls[0].symbol) is None
        assert runtime.opportunity_engine.get(calls[0].symbol).state is WarriorOpportunityState.WAITING_EXECUTION
        runtime.observe(point(), account=account())
        assert len(calls) == 2 and ownership.owner(calls[0].symbol)
        assert runtime.opportunity_engine.get(calls[0].symbol).state is WarriorOpportunityState.REVIEW_PENDING
    finally:
        writer.close()


def test_busy_scalper_review_is_recoverable_without_counting_attempts():
    ownership = SymbolOwnershipRegistry()
    calls = []
    def offer(a):
        calls.append(a)
        if len(calls) == 1:
            return CanonicalScalpSubmission(False, False, False, False, 'AI_REVIEW_BUSY', recoverable=True)
        return CanonicalScalpSubmission(False, True, False, False, 'AI_REVIEW_PENDING',
            a.opportunity.lifecycle_id, review_pending=True, review_candidate_id='candidate')
    engine = QuickScalper(engine=WarriorOpportunityEngine(), ownership=ownership,
        config=QuickScalperConfig(enabled=True), authorize_and_submit=offer)
    engine.observe(snapshot())
    assert ownership.owner('FAST') is None
    assert engine.engine.get('FAST').state is WarriorOpportunityState.WAITING_EXECUTION
    assert not engine.guard._generations
    engine.observe(snapshot())
    assert len(calls) == 2 and ownership.owner('FAST')
    assert engine.engine.get('FAST').state is WarriorOpportunityState.REVIEW_PENDING

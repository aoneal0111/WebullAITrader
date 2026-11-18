"""Real library projection through native review, controlled PAPER and protection."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from app.asset_modules.engine_catalog import EngineId, lifecycle_owner
from app.asset_modules.grok_desktop_producer import DesktopGrokProducer
from app.opportunity_discovery import ACTIVE_STRATEGY_ORDER, MultiStrategyExecutionAdapter
from app.strategies.warrior_momentum.autonomous_paper import lifecycle_identity
from tests.asset_modules.test_controlled_paper_service import setup, fill, NOW
from tests.asset_modules.test_grok_runtime_handoff import warrior, signal, AUTH, FloatProvenance
from tests.asset_modules.test_grok_native_review import create, finished
from tests.asset_modules.test_native_review_branches import arm
from tests.opportunity_discovery.test_execution_adapter import _opportunity, _kwargs


def library_signal(strategy):
    opportunity = _opportunity()
    membership = replace(opportunity.memberships[0], strategy_id=strategy)
    opportunity = replace(opportunity, memberships=(membership,))
    adapted = MultiStrategyExecutionAdapter().adapt(opportunity, **_kwargs(opportunity)).candidate
    # Native test market has a SPY quote at 6 with a 5.9 structural stop.
    adapted = replace(adapted, symbol='SPY', trigger_price=D(6),
                      structural_stop=D('5.9'), risk_per_share=D('.1'))
    setup = adapted.as_warrior_setup()
    value = replace(signal(), taxonomy_strategy_id=setup.taxonomy_strategy_id,
        taxonomy_strategy_memberships=setup.taxonomy_strategy_memberships,
        taxonomy_opportunity_id=setup.taxonomy_opportunity_id,
        taxonomy_opportunity_anchor=setup.taxonomy_opportunity_anchor,
        taxonomy_execution_identity=setup.taxonomy_execution_identity,
        taxonomy_invalidation_reason=setup.taxonomy_invalidation_reason)
    return adapted, value


@pytest.mark.parametrize('strategy', ACTIVE_STRATEGY_ORDER)
def test_real_library_projection_reaches_review_order_fill_and_owned_stop(setup, warrior, strategy):
    commands, bridge, _, _, _, _ = setup
    review, ai = create(setup)
    producer = DesktopGrokProducer(review, permitted=lambda:True)
    adapted, value = library_signal(strategy)
    arm(warrior, value)
    warrior._paper_entry_reviewer = producer.warrior_offer
    try:
        _, _, decision = warrior._open_paper(value, 10, D(1), FloatProvenance.UNKNOWN,
                                             AUTH, review_timestamp=NOW)
        assert decision.reason.value == 'AI_REVIEW_PENDING'
        assert len(producer._requests) == 1
        finished(review)
        producer.drain(warrior, None, at=NOW)
        buys = [o for o in commands.order_book.history() if o.request.side.value=='BUY']
        assert len(buys)==1
        identity = lifecycle_identity(value)
        assert lifecycle_owner(identity) is EngineId.WARRIOR
        assert adapted.execution_identity in identity
        assert buys[0].request.strategy_lifecycle_id==identity
        assert buys[0].request.metadata['taxonomy_strategy_id']==strategy
        assert not warrior._pending_native_reviews and not producer._requests
        fill(commands)
        assert warrior.reconcile_authoritative_protection('SPY', NOW)
        stops=[o for o in commands.order_book.open_orders_for_symbol('SPY')
               if o.request.execution_reason=='STOP']
        assert len(stops)==1 and stops[0].request.quantity==10
        assert stops[0].request.strategy_lifecycle_id==identity
        assert ai.client.requests==2
    finally:
        producer.request_stop()


def test_local_preparation_error_never_latches_uncertain_dispatch(setup, warrior, monkeypatch):
    review, _ = create(setup)
    producer = DesktopGrokProducer(review, permitted=lambda:True)
    value=signal()
    arm(warrior,value)
    warrior._paper_entry_reviewer=producer.warrior_offer
    def invalid(*args,**kwargs):raise ValueError('local placement contract')
    monkeypatch.setattr(review,'prepare',invalid)
    try:
        _,_,decision=warrior._open_paper(value,10,D(1),FloatProvenance.UNKNOWN,AUTH,review_timestamp=NOW)
        assert decision.reason.value!='AI_REVIEW_RECONCILIATION_REQUIRED'
        assert not warrior._pending_native_reviews and not producer._requests
        assert not setup[0].order_book.history()
        assert producer.status()['last_offer']['reason']=='NATIVE_PREPARATION_INVALID'
    finally:producer.request_stop()


def test_existing_bare_hash_ledger_identity_and_protection_are_not_rewritten(setup):
    commands,bridge,service,_,_,_=setup
    old_hash='a'*64
    old_signal=replace(signal(),taxonomy_execution_identity=old_hash,
                       taxonomy_strategy_id='HIGH_OF_DAY_BREAKOUT')
    assert lifecycle_identity(old_signal)==old_hash
    prepared=bridge.prepare_entry_decision(old_signal,10,D(1)).placement
    # Seed an existing ledger row through the deterministic PAPER fixture.
    # New controlled buys with an unowned hash must remain forbidden.
    assert not service.place_order(prepared).success
    result=service.service.place_order(prepared)
    assert result.success
    fill(commands)
    bridge.reconcile()
    bridge.reconcile_protection()
    stops=[o for o in commands.order_book.open_orders_for_symbol('SPY')
           if o.request.execution_reason=='STOP']
    assert len(stops)==1 and stops[0].request.strategy_lifecycle_id==old_hash
    assert stops[0].request.quantity==10


def test_review_status_is_passive_and_exposes_expired_authorization(setup):
    from datetime import timedelta
    from threading import Event
    release=Event();entered=Event()
    def hold(context,answer):
        entered.set();assert release.wait(2)
    review,_=create(setup,hold)
    producer=DesktopGrokProducer(review,permitted=lambda:True)
    _,value=library_signal('HIGH_OF_DAY_BREAKOUT')
    try:
        receipt=producer.warrior_offer(value,10,D(1),float_provenance=FloatProvenance.UNKNOWN,
                                      symbol_authorization=AUTH,evaluated_at=NOW)
        assert receipt.pending and entered.wait(1)
        before=dict(review.pending_states())
        assert producer.status()['review']['pending'][0]['authorization_current']
        setup[-1][0]=NOW+timedelta(seconds=3)
        assert not producer.status()['review']['pending'][0]['authorization_current']
        assert review.pending_states()==before
        assert not setup[0].order_book.history()
    finally:
        producer.request_stop();release.set();finished(review)


def test_observation_and_fresh_quote_refresh_reach_real_native_review_and_controller(setup, warrior, monkeypatch):
    from app.trade_intelligence.taxonomy_paper_bridge import TaxonomyPaperExecutionBridge
    from app.strategies.warrior_momentum.models import SetupState
    from app.strategies.warrior_momentum.execution_quote import ExecutionQuoteSnapshot
    from app.strategies.warrior_momentum.paper_stream_quote import WarriorPaperStreamQuoteSource
    from tests.warrior_momentum.test_forward_capture import point
    template=point();offset=NOW-template.observation.timestamp
    observation=replace(template.observation,symbol='SPY',timestamp=NOW,
                        quote_timestamp=NOW,last_price_timestamp=NOW)
    value=replace(template,observation=observation,
        bars=tuple(replace(b,symbol='SPY',timestamp=b.timestamp+offset) for b in template.bars),
        quote_observed_at=NOW,last_price_observed_at=NOW,evaluation_timestamp=NOW,
        quote_freshness_seconds=D(3),last_price_freshness_seconds=D(3))
    setup[4][0]=ExecutionQuoteSnapshot('SPY',D('10.02'),D('10.01'),D('10.02'),NOW,NOW,NOW,NOW)
    warrior.config=replace(warrior.config,taxonomy_paper_execution_enabled=True,adaptive_context_enabled=True)
    warrior._taxonomy_execution_bridge=TaxonomyPaperExecutionBridge()
    warrior._execution_quote_source=WarriorPaperStreamQuoteSource(lambda _:setup[4][0],clock=lambda:NOW)
    original=warrior.runtime.discover
    def no_native(*args,**kwargs):
        candidate=original(*args,**kwargs)
        return replace(candidate,setup=replace(candidate.setup,state=SetupState.NOT_FORMED,score=D(10)))
    monkeypatch.setattr(warrior.runtime,'discover',no_native)
    review,_=create(setup);producer=DesktopGrokProducer(review,permitted=lambda:True)
    warrior._paper_entry_reviewer=producer.warrior_offer
    try:
        _,returned=warrior.observe(value,account=setup[3][0])
        assert returned is None and len(producer._requests)==1
        assert warrior._execution_quote_source.status()['counts']['rest_requested']==1
        prepared_economics=[(r.entry.prepared.placement.order.limit_price,r.entry.signal.stop_price) for r in producer._requests.values()]
        finished(review);producer.drain(warrior,None,at=NOW)
        buys=[o for o in setup[0].order_book.history() if o.request.side.value=='BUY']
        assert len(buys)==1 and lifecycle_owner(buys[0].request.strategy_lifecycle_id) is EngineId.WARRIOR, (producer.status(),setup[2].last_reason,prepared_economics)
        assert not warrior._pending_native_reviews and warrior.open_paper_symbols==('SPY',)
    finally:producer.request_stop()

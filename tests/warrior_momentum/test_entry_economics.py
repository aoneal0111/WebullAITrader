from dataclasses import replace
from decimal import Decimal as D
from app.strategies.warrior_momentum.entry_economics import remaining_reward_ok
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from tests.warrior_momentum.test_forward_capture import point, scanner, account


def test_small_price_displacement_can_consume_large_fraction_of_trade_reward():
    kw=dict(stop=D('3.98'), targets=(D('4.02'),D('4.04'),D('4.06')),spread=D('.001'))
    assert remaining_reward_ok(entry=D('4'), **kw)
    assert not remaining_reward_ok(entry=D('4.019'), **kw)
    assert not remaining_reward_ok(entry=D('NaN'), **kw)


def test_forward_rejects_consumed_reward_before_submission(tmp_path, monkeypatch):
    store=ForwardCaptureStore(tmp_path/'forward.sqlite3')
    writer=ForwardCaptureWriter(store,flush_interval_seconds=.01)
    service=WarriorForwardCaptureService(store,writer)
    service.config=replace(service.config,adaptive_context_enabled=False,
        trade_management=replace(service.config.trade_management,adaptive_exit_enabled=False))
    original=service.runtime.assess_entry
    def near_target(candidate):
        assessed,signal=original(candidate)
        assert signal is not None
        return assessed,replace(signal,target_levels=(signal.entry_trigger+D('.001'),signal.entry_trigger+D('.002'),signal.entry_trigger+D('.003')))
    monkeypatch.setattr(service.runtime,'assess_entry',near_target)
    opened=[]
    monkeypatch.setattr(service,'_open_paper',lambda *args: opened.append(args))
    try:
        assessed, signal = service.observe(point(),account=account())
        assert signal is None
        assert assessed.status.value == 'INELIGIBLE_FOR_EXECUTION'
        assert not opened
        assert not service._paper
    finally:
        writer.close()


def test_execution_keeps_targets_anchored(tmp_path):
    store=ForwardCaptureStore(tmp_path/'anchor.sqlite3'); writer=ForwardCaptureWriter(store)
    service=WarriorForwardCaptureService(store,writer)
    try:
        value=point(); candidate=service.runtime.discover(value.observation,value.bars,session=value.session)
        _,signal=service.runtime.assess_entry(candidate)
        ask=signal.entry_trigger+D('.01')
        executed=service._execution_entry_signal(point(observation=scanner(bid=ask-D('.01'),ask=ask)),candidate,signal)
        assert executed.entry_trigger == ask
        assert executed.target_levels == signal.target_levels
        assert executed.risk_per_share > signal.risk_per_share
    finally:
        writer.close()


def test_no_depth_fallback_cannot_bypass_reward_gate(tmp_path):
    from types import SimpleNamespace
    store=ForwardCaptureStore(tmp_path/'pursuit.sqlite3'); writer=ForwardCaptureWriter(store)
    service=WarriorForwardCaptureService(store,writer)
    try:
        value=point(); candidate=service.runtime.discover(value.observation,value.bars,session=value.session)
        _,signal=service.runtime.assess_entry(candidate)
        signal=replace(signal,target_levels=(D('10.21'),D('10.23'),D('10.24')))
        service._paper[signal.symbol]=SimpleNamespace(signal=signal,remaining=10,initial_quantity=10)
        calls=[]
        service._paper_entry_replacer=lambda **kw:calls.append(kw)
        service._consider_adaptive_entry_replacement(value,candidate,signal,account())
        assert not calls
    finally:
        writer.close()

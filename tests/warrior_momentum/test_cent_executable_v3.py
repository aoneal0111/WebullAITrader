from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
from types import SimpleNamespace as NS
import pytest

from app.equity_overhaul.paper_experiment import create, load, PaperExperiment
from app.strategies.warrior_momentum.quick_scalper import QuickScalperPolicy, QuickScalperConfig
from app.strategies.warrior_momentum.execution_quote import parse_execution_quote, _symbol_row
from tests.warrior_momentum.test_cent_stream_support import cent_config, make_runtime
from tests.warrior_momentum.test_quick_scalper_runtime import NOW, stream_observation, Quotes, quote
from tests.warrior_momentum.test_quick_scalper import snapshot


def config():
    return replace(cent_config(), cent_fill_anchored_economics=True)


def test_flat_trough_requires_decline_and_distinct_bounce_and_preserves_v2():
    bids = [('4.80',-10),('5.00',-8),('4.93',-6),('4.93',-4),('4.95',0)]
    for cfg, expected in [(cent_config(),'WINDOW_LOW'), (config(),'CONFIRMED_MICRO_PULLBACK')]:
        runtime,_,_ = make_runtime(cfg)
        for bid,offset in bids:
            s = runtime._stream_snapshot(stream_observation(bid,offset),decision_at=NOW,session='REGULAR',context=None)
        assert s.support_source == expected
        if expected == 'CONFIRMED_MICRO_PULLBACK':
            assert s.structural_stop == D('4.93')
            assert s.support_timestamp == NOW-timedelta(seconds=4)
            assert runtime.scalper.policy.assess(s).decision.value == 'EXECUTABLE'


@pytest.mark.parametrize('bids', [
    ['4.80','4.90','4.90','4.95'],  # plateau in an uptrend, no pullback
    ['4.80','5.00','4.93','4.93'],  # no later bounce
    ['4.80','5.00','4.93','4.95','4.93'],  # support retouched at current quote
    ['4.80','5.00','4.93','4.95','4.92'],  # support broken
])
def test_plateau_never_invents_or_keeps_broken_support(bids):
    runtime,_,_ = make_runtime(config())
    for i,bid in enumerate(bids):
        s = runtime._stream_snapshot(stream_observation(bid,i-len(bids)+1),decision_at=NOW,session='REGULAR',context=None)
    assert s.support_source == 'WINDOW_LOW'


def test_same_provider_instant_does_not_confirm_support():
    runtime,_,_ = make_runtime(config())
    for bid,offset in [('4.80',-10),('5.00',-8),('4.93',0),('4.95',0)]:
        s = runtime._stream_snapshot(stream_observation(bid,offset),decision_at=NOW,session='REGULAR',context=None)
    assert s.support_source == 'WINDOW_LOW'


def test_fill_target_reward_does_not_charge_spread_again_but_keeps_cost_and_bid_distance():
    # A .09 spread does NOT become free: full acquisition cost still rejects it.
    narrow = replace(snapshot(ask='5',bid='4.99',stop='4.98',short_range='.20'),
        support_source='CONFIRMED_MICRO_PULLBACK',support_timestamp=NOW-timedelta(seconds=2))
    p = QuickScalperPolicy(config())
    r = p.assess(narrow)
    assert r.round_trip_cost == D('.03')
    assert r.target_move == D('.10')
    assert r.opportunity.remaining_final_target_r == (r.target_move-D('.01'))/D('.03')
    assert r.required_bid_move == r.target_move+D('.02')
    assert p.assess(replace(narrow,bid=D('4.91'),structural_stop=D('4.90'))).reason=='CENT_TARGET_ECONOMICS_WAIT'
    assert p.assess(replace(narrow,bid_timestamp=NOW-timedelta(seconds=6))).reason=='PROVIDER_DATA_STALE'


def test_v3_requires_explicit_new_profile_and_keeps_limits_and_v2_unchanged(tmp_path):
    policies=[]
    for profile in ('CENT_EARLY_V2','CENT_EXECUTABLE_V3'):
        folder=tmp_path/profile
        create(folder,lambda:NOW,profile=profile)
        path,spec=load(folder/'experiment.json',lambda:NOW)
        p=PaperExperiment(path,spec,clock=lambda:NOW,timer=lambda:0)
        policies.append(p)
        assert p.cent_early
        q=p.scalper_config(QuickScalperConfig(enabled=True))
        assert q.cent_fill_anchored_economics == (profile=='CENT_EXECUTABLE_V3')
        assert q.provider_freshness_seconds == 5 and q.minimum_net_target_reward_r == 1
        assert p.status()['protective_sells']=='AVAILABLE'
    assert policies[0].spec['limits']==policies[1].spec['limits']
    assert policies[0].spec['trial_id']!=policies[1].spec['trial_id']
    with pytest.raises(ValueError):QuickScalperConfig(cent_fill_anchored_economics=True)
    raw=policies[1].spec;raw['profile']='CENT_EARLY_V2'
    policies[1].path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):load(policies[1].path,lambda:NOW)


def test_confirmation_diagnostics_explain_exact_stale_reason_without_authorization():
    runtime,bridge,_=make_runtime(config(),Quotes(replace(quote(),bid_timestamp=NOW-timedelta(seconds=9))))
    events=[];runtime._observer_context_tap=NS(record_scalper_confirmation=lambda payload:events.append(payload))
    for bid,offset in [('4.80',-10),('5.00',-8),('4.93',-6),('4.93',-4)]:
        runtime.observe_stream_event(stream_observation(bid,offset),decision_at=NOW,session='REGULAR',execution_permitted=False)
    runtime.observe_stream_event(stream_observation('4.95',0),decision_at=NOW,session='REGULAR')
    assert not bridge.entries
    item=events[0]
    assert item['reason']=='PROVIDER_DATA_STALE' and item['book_regressed']


def test_quote_identity_is_not_relabelled_or_merged():
    epoch=int(NOW.timestamp()*1000)
    raw=dict(symbol='OTHER',price='5',bid='4.99',ask='5',last_trade_time=epoch,quote_time=epoch)
    assert parse_execution_quote('FAST',raw) is None
    good=dict(raw,symbol='FAST')
    assert _symbol_row('FAST',[raw,good]) is good
    assert _symbol_row('FAST',[good,dict(good)]) is None
    assert _symbol_row('FAST',[raw]) is None
    assert parse_execution_quote('FAST',good).bid_timestamp==NOW


def test_v3_grok_lease_is_explicit_and_parent_bound_without_any_requests(tmp_path):
    from app.asset_modules.grok_execution_trial import create as create_lease, ExecutionLease
    create(tmp_path,lambda:NOW,profile='CENT_EXECUTABLE_V3')
    path,spec=load(tmp_path/'experiment.json',lambda:NOW)
    PaperExperiment(path,spec,clock=lambda:NOW,timer=lambda:0)
    lease_path,doc=create_lease(tmp_path,clock=lambda:NOW)
    lease=ExecutionLease(lease_path,clock=lambda:NOW,timer=lambda:0)
    lease.capture_ready=lambda:True
    assert lease.active()
    assert doc['trial_id']==spec['trial_id'] and doc['limits']['paid_attempts']==20
    spec['trial_id']='b'*32
    path.write_text(json.dumps(spec))
    assert not lease.active()

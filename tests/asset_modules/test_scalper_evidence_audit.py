from dataclasses import asdict,replace
from datetime import timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path

import pytest
from app.asset_modules.daily_research_journal import DailyJournal,safe
from app.asset_modules.scalper_evidence_audit import typed,edge_terms,reconstructed_preview,book_state,audit,POLICY_FILES
from app.strategies.warrior_momentum.quick_scalper import QuickScalperConfig,QuickScalpSnapshot,QuickScalperPolicy
from tests.warrior_momentum.test_quick_scalper import snapshot,NOW


def test_snapshot_and_native_policy_round_trip_without_changed_timestamps():
    original=snapshot();config=QuickScalperConfig(enabled=True)
    assert typed(QuickScalpSnapshot,safe(original))==original
    assert typed(QuickScalperConfig,safe(config))==config
    with pytest.raises(ValueError):typed(QuickScalpSnapshot,{**safe(original),'unexpected':1})
    invalid=safe(original);invalid['stream_sample_count']=True
    with pytest.raises(ValueError):typed(QuickScalpSnapshot,invalid)
    raw=safe(original);raw['last']='NaN'
    with pytest.raises(ValueError):typed(QuickScalpSnapshot,raw)


def test_attribution_uses_real_policy_and_accounts_for_spread_once_per_term():
    policy=QuickScalperPolicy();snap=snapshot(ask='5',bid='4.99',stop='4.95',short_range='.05')
    result,metrics,targets=edge_terms(snap,policy)
    assert result==policy.assess(snap)
    assert metrics['round_trip_cost']==D('.03')
    assert result.target_move==max(targets.values())
    assert result.required_bid_move==result.target_move+D('.02')
    assert metrics['edge_margin']==result.expected_move-result.required_bid_move
    assert metrics['ask_anchored_target_net_before_fees']-metrics['policy_target_net']==D('.01')
    assert metrics['modeled_slippage_both_sides']==D('.02')
    assert result.reason=='INSUFFICIENT_NET_EXECUTABLE_EDGE'


def test_regressed_rest_does_not_rescue_an_already_stale_stream():
    source=replace(snapshot(),decision_at=NOW,bid_timestamp=NOW-timedelta(seconds=8),ask_timestamp=NOW-timedelta(seconds=8))
    quote=dict(last='5',bid='4.96',ask='5',last_timestamp=str(NOW),
        bid_timestamp=str(NOW-timedelta(seconds=9)),ask_timestamp=str(NOW-timedelta(seconds=9)))
    refreshed=reconstructed_preview(source,quote,NOW+timedelta(seconds=.3))
    assert refreshed.bid_timestamp==NOW-timedelta(seconds=9)
    assert book_state(source.decision_at,source.bid_timestamp,source.ask_timestamp)=='STALE'
    assert QuickScalperPolicy().assess(refreshed).reason=='PROVIDER_DATA_STALE'
    assert book_state(NOW,NOW+timedelta(seconds=1),NOW)=='FUTURE'


def make_session(tmp_path,*,mismatch=False):
    root=tmp_path/'session';root.mkdir()
    source=Path(__file__).resolve().parents[2]
    hashes={p:sha256((source/p).read_bytes()).hexdigest() for p in POLICY_FILES}
    baseline='a'*64
    (root/'research-manifest.json').write_text(json.dumps(dict(scope='NORMAL_DESKTOP_DAILY_RESEARCH_V1',
        source_files=hashes,baseline_sha256=baseline)))
    (root/'research-summary.json').write_text(json.dumps(dict(policy_unchanged=True)))
    journal=DailyJournal(root/'journal',baseline,clock=lambda:NOW)
    config=QuickScalperConfig(enabled=True);policy=QuickScalperPolicy(config)
    journal.submit('NATIVE_POLICY_BASELINE',dict(settings=dict(scalper=asdict(config))))
    s=replace(snapshot(),setup_type='QUICK_SCALP_STREAM',observed_bid_advance=D('.05'),observed_ask_advance=D('.04'))
    result=policy.assess(s)
    journal.submit('STREAM_ASSESSMENT',dict(snapshot=s,reason='WRONG' if mismatch else result.reason,decision=result.decision))
    quote=dict(last='5.00',bid='4.96',ask='5.00',last_timestamp=NOW,
        bid_timestamp=NOW-timedelta(seconds=8),ask_timestamp=NOW-timedelta(seconds=8))
    journal.submit('ASSESSMENT',dict(engine='QUICK_SCALPER',preview=s,quote=quote,assessed_at=NOW,
        baseline_result='BLOCKED'))
    journal.close();return root


def test_complete_recorded_replay_reconciles_and_preserves_sources(tmp_path):
    root=make_session(tmp_path)
    before={str(p):sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    result=audit(root)
    assert result['source_replay_matches']
    assert result['stream_assessments']==result['confirmations']==1
    assert result['confirmation_reasons']=={'PROVIDER_DATA_STALE':1}
    assert result['paired_quotes']['REST_BOOK_REGRESSED']==1
    assert result['paired_quotes']['FRESH_STREAM_AT_CONFIRMATION_WITH_REGRESSED_REST']==1
    assert result['orders']==result['network_requests']==result['grok_requests']==0
    assert before=={str(p):sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def test_mismatch_is_reported_without_faking_success(tmp_path):
    result=audit(make_session(tmp_path,mismatch=True))
    assert not result['source_replay_matches'] and result['replay_mismatches'][0]['recorded']=='WRONG'


@pytest.mark.parametrize('changed',['source','chain','policy'])
def test_mutated_evidence_or_policy_is_refused(tmp_path,changed):
    root=make_session(tmp_path)
    if changed=='source':
        path=root/'research-manifest.json';raw=json.loads(path.read_text());raw['source_files'][POLICY_FILES[0]]='b'*64
        path.write_text(json.dumps(raw))
    elif changed=='policy':(root/'research-summary.json').write_text('{"policy_unchanged":false}')
    else:
        path=next((root/'journal').glob('events-*.jsonl'));path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError):audit(root)


def test_reconstructed_confirmation_matches_the_actual_native_runtime(monkeypatch):
    from tests.warrior_momentum.test_quick_scalper_runtime import Bridge,Quotes,quote,snapshot as runtime_snapshot,account,NOW as AT
    from app.strategies.warrior_momentum.quick_scalper_runtime import QuickScalperPaperRuntimeAdapter
    from app.paper_trading.order_book import PaperOrderBook
    native=[];original=QuickScalperPolicy.assess
    def observed(self,value):
        native.append(value);return original(self,value)
    monkeypatch.setattr(QuickScalperPolicy,'assess',observed)
    q=quote();value=runtime_snapshot()
    runtime=QuickScalperPaperRuntimeAdapter(config=QuickScalperConfig(enabled=True),bridge=Bridge(),
        order_book=PaperOrderBook(),account_context_source=account,position_quantity_source=lambda _:D(0),
        execution_quote_source=Quotes(q),clock=lambda:AT)
    runtime._snapshots[(value.symbol,value.generation_id)]=value
    runtime.scalper.observe(value)
    assert len(native)>=2
    assert native[-1]==reconstructed_preview(value,safe(q),AT)

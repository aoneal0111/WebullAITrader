from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
import json
import pytest

from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.supervisor import CryptoSupervisor
from app.crypto_research.models import CryptoPair,CryptoObservation,CryptoResearchDecision,CryptoResearchRegime
from app.crypto_research.analysis import calculate_features
from app.crypto_research.runtime import CryptoResearchRuntime
from tests.asset_modules.test_grok_execution_trial import pilot,NOW,configuration


def quote(at=NOW,bid='100',ask='100.1'):
    return CryptoObservation(CryptoPair.configured('BTC/USD'),at,D(100),D(bid),D(ask),None)


def decision():
    row=quote()
    return CryptoResearchDecision('1',row.pair,NOW,NOW,row.price,row.bid,row.ask,D('.1'),None,
        calculate_features((row,),cutoff=NOW),(),D(0),(),1,CryptoResearchRegime.US)


def test_runtime_execution_refresh_only_reads_owned_discovered_pairs(tmp_path):
    calls=[]
    provider=NS(snapshots=lambda pairs:calls.append(pairs) or (quote(),))
    runtime=CryptoResearchRuntime(enabled=True,provider=provider,path=tmp_path/'crypto.jsonl')
    runtime._pairs=(CryptoPair.configured('BTC/USD'),)
    try:
        assert runtime.execution_snapshots(('ETH/USD',))==() and not calls
        assert runtime.execution_snapshots(('BTC/USD',))==(quote(),) and len(calls)==1
        with pytest.raises(ValueError):runtime.execution_snapshots(('BTC/USD','BTC/USD'))
        with pytest.raises(ValueError):runtime.execution_snapshots(('BTC/USD','ETH/USD','SOL/USD'))
        runtime.enabled=False
        assert runtime.execution_snapshots(('BTC/USD',))==() and len(calls)==1
    finally:runtime.close()


@pytest.mark.parametrize('fresh',[False,True])
def test_crypto_uses_post_model_refresh_not_cached_input_quote(tmp_path,monkeypatch,fresh):
    import app.asset_modules.supervisor as module
    now=[NOW];records=[];calls=[];contexts=[]
    monkeypatch.setattr(module,'datetime',NS(now=lambda tz=None:now[0]))
    paper=CryptoPaper(tmp_path/'paper.sqlite3');paper.max_position=D(100);paper.max_loss=D(1)
    class Provider:
        def propose(self,context):
            contexts.append(context);now[0]+=timedelta(seconds=20)
            return [dict(symbol='BTC/USD',action='BUY',notional='5',stop='90',target='130',reason='fixture')]
    def refresh(symbols):
        calls.append(symbols)
        return (quote(now[0]-timedelta(seconds=.2) if fresh else NOW),)
    supervisor=CryptoSupervisor(paper,lambda:(decision(),),Provider(),maximum_quote_age_seconds=2,
        execution_limits={'maximum_position_dollars':'100','maximum_initial_risk':'1'},
        history_source=lambda symbol,at:(),execution_quote_source=refresh,evidence_sink=lambda kind,payload:records.append(kind))
    try:
        supervisor.entries_enabled=True;supervisor.tick()
        assert calls==[('BTC/USD',)]
        assert bool(paper.snapshot()['positions']) is fresh
        assert 'completed_provider_bars' in contexts[0] and 'execution_limits' in contexts[0]
        assert 'CRYPTO_EXECUTION_QUOTES' in records and 'CRYPTO_GROK_DISPATCH' in records
        # Closing new entry permission never removes existing protection.
        supervisor.entries_enabled=False
        if fresh:
            now[0]+=timedelta(seconds=2)
            supervisor._protect({'BTC/USD':quote(now[0],bid='140',ask='140.1')},now[0])
            assert not paper.snapshot()['positions'] and len(contexts)==1
    finally:paper.close()


def test_protection_refuses_old_quote_in_execution_pilot(tmp_path):
    paper=CryptoPaper(tmp_path/'paper.sqlite3')
    try:
        paper.apply(dict(id='one',symbol='BTC/USD',action='BUY',notional='5',stop='90',target='130'),{'BTC/USD':quote()},now=NOW)
        supervisor=CryptoSupervisor(paper,lambda:(),maximum_quote_age_seconds=2)
        supervisor._protect({'BTC/USD':quote(NOW,bid='140',ask='140.1')},NOW+timedelta(seconds=3))
        assert paper.snapshot()['positions']
        supervisor._protect({'BTC/USD':quote(NOW+timedelta(seconds=4),bid='140',ask='140.1')},NOW+timedelta(seconds=4))
        assert not paper.snapshot()['positions']
    finally:paper.close()


def test_pilot_ledger_also_refuses_stale_operator_sell_without_changing_default(tmp_path):
    paper=CryptoPaper(tmp_path/'paper.sqlite3')
    try:
        assert paper.maximum_quote_age_seconds==90
        paper.maximum_quote_age_seconds=2
        paper.apply(dict(id='one',symbol='BTC/USD',action='BUY',notional='5',stop='90',target='130'),{'BTC/USD':quote()},now=NOW)
        with pytest.raises(ValueError,match='stale'):
            paper.apply(dict(id='sell',symbol='BTC/USD',action='SELL'),{'BTC/USD':quote()},now=NOW+timedelta(seconds=3))
        assert paper.snapshot()['positions']
        paper.apply(dict(id='sell',symbol='BTC/USD',action='SELL'),{'BTC/USD':quote(NOW+timedelta(seconds=4))},now=NOW+timedelta(seconds=4))
        assert not paper.snapshot()['positions']
    finally:paper.close()


def test_explicit_execution_recording_is_distinct_from_passive_observer(pilot,monkeypatch,tmp_path):
    from app.asset_modules.daily_research import DailyResearch
    from app.asset_modules.grok_execution_trial import ENV
    from app.equity_overhaul.paper_experiment import ENV as PARENT_ENV
    root,lease,_=pilot
    monkeypatch.setenv(ENV,str(lease.path));monkeypatch.setenv(PARENT_ENV,str(root/'experiment.json'))
    recorder=DailyResearch(configuration(),root=tmp_path/'recordings',clock=lambda:NOW)
    try:
        manifest=json.loads((recorder.directory/'research-manifest.json').read_text())
        assert manifest['grok_order_review_enabled'] and manifest['automatic_grok_requests_on_operator_start']
        assert manifest['grok_execution_trial']['trial_id']==lease.doc['trial_id']
        assert manifest['live_trading_enabled'] is False
    finally:recorder.close()


def test_execution_launchers_do_not_embed_credentials_or_overwrite_originals(pilot,tmp_path,monkeypatch):
    from app.asset_modules.grok_execution_trial import write_launchers
    root,lease,_=pilot;repo=tmp_path/'repo';repo.mkdir()
    (repo/'.env').write_text('XAI_API_KEY=private-test-value\nATLAS_GROK_MODEL=grok-test\n')
    original=root/'Start-Atlas-Paper-Trial.ps1';original.write_text('original native launcher')
    before=(root/'experiment.json').read_bytes();budget=(root/'entry-attempts.sqlite3').read_bytes()
    monkeypatch.delenv('XAI_API_KEY',raising=False);monkeypatch.delenv('ATLAS_GROK_MODEL',raising=False)
    result=write_launchers(repo,root,clock=lambda:NOW)
    script=(root/'Start-Atlas-Grok-Execution.ps1').read_text()
    assert 'private-test-value' not in script and 'private-test-value' not in json.dumps(result)
    assert "'ATLAS_GROK_PAPER_ENTRIES_ENABLED' = 'true'" in script
    assert "'LIVE_TRADING_ENABLED' = 'false'" in script
    assert original.read_text()=='original native launcher'
    assert (root/'experiment.json').read_bytes()==before and (root/'entry-attempts.sqlite3').read_bytes()==budget
    assert "'--backtest-review','--watch'" in (root/'Review-Grok-Backtest.ps1').read_text()

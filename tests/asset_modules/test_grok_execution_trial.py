from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS
import json
import sqlite3
import pytest

from app.asset_modules.grok import GrokUnavailable
from app.asset_modules.grok_execution_trial import ExecutionLease,BudgetClient,create,prior_attempts,configured,write_launchers,ENV
from app.configuration.loader import load_configuration
from app.equity_overhaul.paper_experiment import create as create_parent,load,PaperExperiment,ENV as PARENT_ENV

NOW=datetime(2026,10,30,15,0,tzinfo=UTC)


@pytest.fixture
def pilot(tmp_path):
    root=tmp_path/'trial';create_parent(root,lambda:NOW,profile='CENT_EARLY_V2')
    path,spec=load(root/'experiment.json',lambda:NOW)
    PaperExperiment(path,spec,clock=lambda:NOW)
    path,_=create(root,clock=lambda:NOW);now=[NOW]
    lease=ExecutionLease(path,clock=lambda:now[0],timer=lambda:(now[0]-NOW).total_seconds())
    return root,lease,now


class Client:
    model='grok-test'
    requests=0
    def request(self,*args):self.requests+=1;return {'selected_ids':[],'reason':'test'}
    def usage(self):return dict(input_tokens=4,output_tokens=2,responses_with_usage=self.requests,responses_without_usage=0)
    def diagnostics(self):return dict(attempts_completed=self.requests)


def test_one_durable_budget_for_all_roles_and_restarts_with_private_context_redaction(pilot):
    root,lease,now=pilot;client=Client();bounded=BudgetClient(client,lease)
    original=(root/'experiment.json').read_bytes();entry_budget=(root/'entry-attempts.sqlite3').read_bytes()
    roles=['WARRIOR_MOMENTUM_V1','QUICK_SCALPER','CRYPTO','MASTER','LEARNING']
    for n in range(20):
        now[0]=NOW+timedelta(seconds=61*(n//5))
        bounded.request(roles[n%5],'test',dict(mode='PAPER',password='never-store-private-value'),{})
    restored=ExecutionLease(lease.path,clock=lambda:now[0])
    assert restored.request_count()==20
    with pytest.raises(GrokUnavailable,match='PAID_LIMIT'):
        BudgetClient(Client(),restored).request('MASTER','test',{'mode':'PAPER'},{})
    with sqlite3.connect(lease.db_path) as db:
        rows=db.execute('SELECT context,result FROM requests').fetchall()
    assert all('never-store-private-value' not in a for a,b in rows)
    assert json.loads(rows[-1][1])['usage']['input_tokens']==4
    assert (root/'experiment.json').read_bytes()==original and (root/'entry-attempts.sqlite3').read_bytes()==entry_budget


def test_failed_and_crashed_calls_consume_budget_without_retry(pilot):
    _,lease,_=pilot
    class Bad(Client):
        def request(self,*args):raise GrokUnavailable('TRANSPORT_UNAVAILABLE')
    with pytest.raises(GrokUnavailable):BudgetClient(Bad(),lease).request('CRYPTO','test',{'mode':'PAPER'},{})
    lease.reserve_request('MASTER',{'mode':'PAPER'})  # crashed before result
    assert ExecutionLease(lease.path,clock=lambda:NOW).request_count()==2
    with pytest.raises(GrokUnavailable,match='COOLDOWN'):lease.reserve_request('MASTER',{'mode':'PAPER'})


@pytest.mark.parametrize('revocation',['expiry','pause','delete','edit','parent','clock'])
def test_revoked_model_completion_never_becomes_advice(pilot,revocation):
    root,lease,now=pilot
    class Revoke(Client):
        def request(self,*args):
            if revocation=='expiry':now[0]+=timedelta(minutes=31)
            elif revocation=='pause':(root/'PAUSE_GROK_EXECUTION').touch()
            elif revocation=='delete':lease.path.unlink()
            elif revocation=='edit':lease.path.write_text('{}')
            elif revocation=='parent':(root/'experiment.json').write_text('{}')
            else:lease._timer_start-=10
            return {'selected_ids':['arbitrary']}
    with pytest.raises(GrokUnavailable,match='REVOKED'):BudgetClient(Revoke(),lease).request('MASTER','test',{'mode':'PAPER'},{})


def test_prior_intention_calls_are_retained_and_incomplete_accounting_is_refused(tmp_path):
    folder=tmp_path/'grok-order-intentions';folder.mkdir()
    with pytest.raises(ValueError,match='SUMMARY_REQUIRED'):prior_attempts(tmp_path)
    summary={'requests_scheduled':7,'provider_requests_attempted':6}
    (folder/'summary.json').write_text(json.dumps(summary))
    archive=tmp_path/'grok-order-intentions-archive-one';archive.mkdir()
    (archive/'summary.json').write_text(json.dumps({'requests_scheduled':3}))
    assert prior_attempts(tmp_path)==10


def request(identity='crypto-one',**changes):
    return NS(engine=NS(value='CRYPTO'),quote_at=NOW,capital=D(50),loss_budget=D('.5'),quantity=D('.01'),
        command_id=identity,plan_fingerprint='a'*64,**changes)


def test_crypto_counts_against_original_fifty_attempts_and_dollar_budget(pilot):
    root,lease,_=pilot;lease.capture_ready=lambda:True
    dbpath=root/'entry-attempts.sqlite3'
    with sqlite3.connect(dbpath) as db:
        for n in range(49):db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(lease.doc['trial_id'],str(n),'1','{}'))
    assert lease.claim_crypto(request(),NOW)
    assert lease.claim_crypto(request(),NOW)  # same reservation, not another fill
    assert not lease.claim_crypto(request('another'),NOW)
    assert not lease.claim_crypto(NS(**{**vars(request()),'plan_fingerprint':'b'*64}),NOW)
    with sqlite3.connect(dbpath) as db:assert db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==50


@pytest.mark.parametrize('change',[{'capital':D(101)},{'loss_budget':D('1.01')},{'quote_at':NOW-timedelta(seconds=3)},
    {'quote_at':NOW+timedelta(seconds=1)},{'quantity':D(0)}])
def test_crypto_bounds_and_freshness_before_reservation(pilot,change):
    root,lease,_=pilot;lease.capture_ready=lambda:True
    assert not lease.claim_crypto(NS(**{**vars(request()),**change}),NOW)
    with sqlite3.connect(root/'entry-attempts.sqlite3') as db:assert db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==0


def test_crypto_requires_healthy_recording_even_when_lease_active(pilot):
    _,lease,_=pilot
    assert lease.active() and not lease.claim_crypto(request(),NOW)
    lease.capture_ready=lambda:False
    assert not lease.claim_crypto(request(),NOW)


def configuration(**changes):
    return load_configuration({'WEBULL_TRADING_ENVIRONMENT':'PAPER','LIVE_TRADING_ENABLED':'false',
        'ATLAS_GROK_PAPER_ENTRIES_ENABLED':'true','ATLAS_PAPER_CONTROLLER_ENABLED':'true',
        'ATLAS_DAILY_RESEARCH_ENABLED':'true','ATLAS_CRYPTO_SHARED_ALLOCATION_ENABLED':'true',**changes})


@pytest.mark.parametrize('key,value',[('LIVE_TRADING_ENABLED','true'),('WEBULL_TRADING_ENVIRONMENT','TEST'),
    ('ATLAS_GROK_PAPER_ENTRIES_ENABLED','false'),('ATLAS_PAPER_CONTROLLER_ENABLED','false'),
    ('ATLAS_DAILY_RESEARCH_ENABLED','false'),('ATLAS_CRYPTO_SHARED_ALLOCATION_ENABLED','false'),
    ('ATLAS_CRYPTO_NATIVE_OBSERVER_ENABLED','true')])
def test_execution_requires_complete_explicit_safe_configuration(pilot,monkeypatch,key,value):
    root,lease,_=pilot;monkeypatch.setenv(ENV,str(lease.path));monkeypatch.setenv(PARENT_ENV,str(root/'experiment.json'))
    with pytest.raises(ValueError):configured(configuration(**{key:value}),clock=lambda:NOW)


def test_configuration_binds_exact_parent_and_disabled_mode_stays_inert(pilot,monkeypatch):
    root,lease,_=pilot
    monkeypatch.delenv(ENV,raising=False);assert configured(configuration(),clock=lambda:NOW) is None
    monkeypatch.setenv(ENV,str(lease.path));monkeypatch.setenv(PARENT_ENV,str(root/'experiment.json'))
    assert configured(configuration(),clock=lambda:NOW).doc['trial_id']==lease.doc['trial_id']
    monkeypatch.setenv(PARENT_ENV,str(root/'different.json'))
    with pytest.raises(ValueError):configured(configuration(),clock=lambda:NOW)


def test_current_native_and_master_path_still_crosses_qualification_and_shared_budget(setup,tmp_path):
    from app.asset_modules.grok_paper_entries import GrokPaperEntries,PreparedEntry
    from app.asset_modules.grok_coordinator import Candidate,EngineId
    from app.equity_overhaul.admission import QualifiedEquityTradingService
    from tests.asset_modules.test_grok_paper_entries import suite
    from tests.asset_modules.test_controlled_paper_service import placement,NOW as AT
    commands,_,service,_,_,clock=setup
    root=tmp_path/'native-trial';create_parent(root,lambda:AT,profile='CENT_EARLY_V2')
    path,spec=load(root/'experiment.json',lambda:AT)
    policy=PaperExperiment(path,spec,clock=lambda:clock[0]);policy.capture_ready=lambda:True
    policy.bind(NS(_code={'WARRIOR_MOMENTUM_V1':'a'*64,'QUICK_SCALPER':'c'*64},
        _configuration={'WARRIOR_MOMENTUM_V1':'b'*64,'QUICK_SCALPER':'d'*64}))
    service.service=QualifiedEquityTradingService(service.service,policy)
    p=placement(commands,strategy_owner='WARRIOR_MOMENTUM',strategy_family='MICRO_PULLBACK',
        strategy_trial_id=spec['trial_id'],strategy_code_fingerprint='a'*64,strategy_configuration_fingerprint='b'*64)
    candidate=Candidate(EngineId.WARRIOR,'one',AT+timedelta(seconds=60),'{}',lifecycle_id=p.order.strategy_lifecycle_id)
    ai,_=suite();dispatcher=GrokPaperEntries(ai,service,authorize=lambda _:True,clock=lambda:clock[0])
    result=dispatcher.review_and_submit([PreparedEntry(candidate,p)],engine_evidence={},account_evidence={'mode':'PAPER'})
    assert result.outcomes[0].state=='SUBMITTED' and len(commands.order_book.history())==1
    with sqlite3.connect(root/'entry-attempts.sqlite3') as db:assert db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==1


@pytest.mark.parametrize('new_window',[False,True])
def test_real_desktop_pilot_shares_budget_capture_controller_and_crypto_without_starting(pilot,tmp_path,monkeypatch,new_window):
    import app.composition.desktop as desktop
    import app.composition.asset_modules as markets
    import app.configuration as config_module
    import app.asset_modules.grok_execution_trial as execution_module
    import app.equity_overhaul.paper_experiment as experiment_module
    import app.asset_modules.daily_research as recording
    from app.asset_modules.daily_research import DailyResearch
    root,lease,_=pilot
    at=NOW+timedelta(minutes=31) if new_window else NOW
    monkeypatch.setenv('XAI_API_KEY','offline-test-secret');monkeypatch.setenv('ATLAS_GROK_MODEL','grok-4.7')
    if new_window:
        from app.asset_modules.grok_execution_trial import prepare_next_window
        path,_=prepare_next_window(tmp_path,root,clock=lambda:at)
        lease=ExecutionLease(path,clock=lambda:at)
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls,tz=None):return at
    monkeypatch.setattr(execution_module,'datetime',FrozenDatetime)
    monkeypatch.setattr(experiment_module,'datetime',FrozenDatetime)
    monkeypatch.setenv(ENV,str(lease.path));monkeypatch.setenv(PARENT_ENV,str(root/'experiment.json'))
    config=configuration(EXECUTION_DATABASE_PATH=str(tmp_path/'execution.db'),
        WARRIOR_FORWARD_CAPTURE_PATH=str(tmp_path/'warrior.db'),CRYPTO_DISCOVERY_PATH=str(tmp_path/'crypto.jsonl'),
        QUICK_SCALPER_ENABLED='true',WARRIOR_FORWARD_PAPER_ENABLED='true')
    monkeypatch.setattr(desktop,'load_configuration',lambda:config)
    monkeypatch.setattr(config_module,'load_configuration',lambda:config)
    monkeypatch.setattr(recording,'configured_research',lambda *args:DailyResearch(config,root=tmp_path/'recordings',clock=lambda:at))
    composition=desktop.create_desktop_composition(paper_persistence_path=tmp_path/'paper.db',paper_clock=lambda:at)
    modules=None
    try:
        modules=markets.create_asset_modules(composition)
        client=composition.grok_suite.client
        assert composition.grok_suite.single_pass and composition.grok_suite.crypto.compact
        assert client.client.reasoning_effort=='low' and client.lease._registered_window==new_window
        assert client.client.maximum_output_tokens==2048
        assert isinstance(client,BudgetClient) and client.lease.request_count()==0 and client.requests==0
        assert modules.grok_suite.client is client
        assert modules.crypto_supervisor.provider.client is client
        assert modules.crypto_supervisor.maximum_quote_age_seconds==2
        assert modules.crypto_supervisor.paper.max_position==100 and modules.crypto_supervisor.paper.max_loss==1
        assert modules.crypto_supervisor.paper.maximum_quote_age_seconds==2
        assert modules.crypto_supervisor.execution_quote_source is not None
        assert not modules.crypto_supervisor.entries_enabled and modules.crypto_supervisor.thread is None
        assert composition.daily_research.grok_execution is not None
        producer=composition.warrior_forward_sidecar._grok_native_producer
        assert producer.review.handoff.worker.dispatcher.service is composition.paper_controller_service
        assert composition.paper_controller_service.service.policy.grok_execution is not None
        assert not composition.runtime_service.is_active and not composition.paper_order_book.history()
    finally:
        if modules is not None:
            modules.crypto_supervisor.close();modules.crypto_supervisor.paper.close()
        composition.close()


# The existing fixture exercises the actual controller and durable PAPER gateway.
from tests.asset_modules.test_controlled_paper_service import setup

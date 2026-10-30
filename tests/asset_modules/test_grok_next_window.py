from datetime import timedelta
from decimal import Decimal as D
import json
import sqlite3

import pytest

from app.asset_modules.grok import GrokUnavailable
from app.asset_modules.grok_execution_trial import ExecutionLease,prepare_next_window,next_window_preflight
from tests.asset_modules.test_grok_execution_trial import pilot,NOW,request

AT=NOW+timedelta(minutes=31)


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv('XAI_API_KEY','offline-fixture-key')
    monkeypatch.setenv('ATLAS_GROK_MODEL','grok-4.7')


def rows(path,table):
    with sqlite3.connect(path) as db:return db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall()


def test_explicit_next_window_preserves_parent_original_lease_calls_and_entry_rows(pilot,environment):
    root,old,_=pilot
    old.reserve_request('MASTER',{'mode':'PAPER'})
    with sqlite3.connect(root/'entry-attempts.sqlite3') as db:
        db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(old.doc['trial_id'],'prior-native','50','{}'))
    for name in ('Start-Atlas-Grok-Execution.ps1','Start-Atlas-Paper-Trial.ps1'):(root/name).write_text('preserve original')
    before={name:(root/name).read_bytes() for name in ('experiment.json','grok-execution-lease.json','entry-attempts.sqlite3',
        'Start-Atlas-Grok-Execution.ps1','Start-Atlas-Paper-Trial.ps1')}
    requests=rows(old.db_path,'requests')
    path,report=prepare_next_window(root,root,clock=lambda:AT)
    lease=ExecutionLease(path,clock=lambda:AT)
    assert lease.active() and lease._registered_window and lease.request_count()==1
    assert lease.doc['id']!=old.doc['id'] and lease.doc['trial_id']==old.doc['trial_id']
    assert lease.doc['prior_paid_attempts']==old.doc['prior_paid_attempts']
    assert report['continuation_preflight']['entry_attempts_remaining']==49
    assert report['continuation_preflight']['paid_attempts_remaining']==19
    assert report['execution_reasoning_effort']=='low' and report['budgets_reset'] is False
    assert rows(old.db_path,'requests')==requests
    assert all((root/name).read_bytes()==value for name,value in before.items())
    launcher=root/('Start-Atlas-Grok-Execution-'+lease.doc['id']+'.ps1')
    assert str(path) in launcher.read_text() and 'app.gui.app' in launcher.read_text()
    assert not ExecutionLease(old.path,clock=lambda:AT).active()
    # Both objects spend the SAME request allowance; no private per-window reset.
    lease.reserve_request('CRYPTO',{'mode':'PAPER'})
    assert old.request_count()==lease.request_count()==2


def test_window_cannot_be_implicitly_renewed_or_overlap_existing_window(pilot,environment):
    root,old,_=pilot
    with pytest.raises(ValueError,match='NOT_EXPIRED'):prepare_next_window(root,root,clock=lambda:NOW)
    path,_=prepare_next_window(root,root,clock=lambda:AT)
    with pytest.raises(ValueError,match='NOT_EXPIRED'):prepare_next_window(root,root,clock=lambda:AT+timedelta(minutes=1))
    assert not ExecutionLease(path,clock=lambda:AT+timedelta(minutes=31)).active()
    # Explicit later preparation appends a record, preserving all previous evidence.
    second,_=prepare_next_window(root,root,clock=lambda:AT+timedelta(minutes=31))
    assert second!=path and path.is_file() and len(rows(old.db_path,'execution_windows'))==2


def test_concurrent_explicit_preparations_cannot_approve_two_overlapping_windows(pilot,environment):
    from concurrent.futures import ThreadPoolExecutor
    root,old,_=pilot
    def attempt():
        try:return prepare_next_window(root,root,clock=lambda:AT)[0]
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:attempt(),range(2)))
    assert sum(value is not None for value in results)==1
    assert len(rows(old.db_path,'execution_windows'))==1


@pytest.mark.parametrize('change',['document','registration','anchor','pause'])
def test_changed_or_revoked_window_fails_closed(pilot,environment,change):
    root,_,_=pilot;path,_=prepare_next_window(root,root,clock=lambda:AT)
    lease=ExecutionLease(path,clock=lambda:AT)
    if change=='document':
        altered=dict(lease.doc,expires_at=(AT+timedelta(minutes=31)).isoformat());path.write_text(json.dumps(altered))
    elif change=='registration':
        with sqlite3.connect(lease.db_path) as db:db.execute('DELETE FROM execution_windows')
    elif change=='anchor':(root/'grok-execution-lease.json').write_text('{}')
    else:(root/'PAUSE_GROK_EXECUTION').touch()
    assert not lease.active()
    with pytest.raises(GrokUnavailable):lease.reserve_request('MASTER',{'mode':'PAPER'})


def test_unregistered_copied_lease_cannot_create_a_new_window(pilot):
    root,old,_=pilot
    path=root/'unapproved.json'
    doc=dict(old.doc,id='a'*32,created_at=AT.isoformat(),expires_at=(AT+timedelta(minutes=30)).isoformat())
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='IDENTITY_CONFLICT'):ExecutionLease(path,clock=lambda:AT)
    assert old.request_count()==0


@pytest.mark.parametrize('budget',['calls','entries','notional'])
def test_exhausted_budget_is_not_reset_for_a_new_window(pilot,environment,budget):
    root,old,_=pilot
    if budget=='calls':
        with sqlite3.connect(old.db_path) as db:
            for n in range(20):db.execute('INSERT INTO requests VALUES (?,?,?,?,NULL)',(str(n),'MASTER',NOW.isoformat(),'{}'))
    else:
        with sqlite3.connect(root/'entry-attempts.sqlite3') as db:
            for n in range(50 if budget=='entries' else 1):
                db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(old.doc['trial_id'],str(n),'1' if budget=='entries' else '5000','{}'))
    with pytest.raises(ValueError,match='EXHAUSTED'):prepare_next_window(root,root,clock=lambda:AT)
    assert not list(root.glob('grok-execution-window-*.json'))


def test_shared_crypto_attempt_limit_carries_into_approved_next_window(pilot,environment):
    root,old,_=pilot
    with sqlite3.connect(root/'entry-attempts.sqlite3') as db:
        for n in range(49):db.execute('INSERT INTO attempts VALUES (?,?,?,?)',(old.doc['trial_id'],str(n),'1','{}'))
    path,_=prepare_next_window(root,root,clock=lambda:AT)
    lease=ExecutionLease(path,clock=lambda:AT);lease.capture_ready=lambda:True
    r=request();r.quote_at=AT
    assert lease.claim_crypto(r,AT)
    another=request('second');another.quote_at=AT
    assert not lease.claim_crypto(another,AT)
    assert len(rows(root/'entry-attempts.sqlite3','attempts'))==50


def test_next_window_stops_at_original_parent_expiry(pilot,environment):
    root,_,_=pilot;at=NOW+timedelta(hours=7,minutes=50)
    path,_=prepare_next_window(root,root,clock=lambda:at)
    lease=ExecutionLease(path,clock=lambda:at)
    assert lease.doc['expires_at']==(NOW+timedelta(hours=8)).isoformat()


def test_new_window_cannot_recreate_a_missing_shared_call_ledger(pilot,environment):
    root,old,_=pilot;path,_=prepare_next_window(root,root,clock=lambda:AT)
    old.db_path.rename(root/'retained-budget.sqlite3')
    with pytest.raises(ValueError,match='EXISTING_SHARED'):ExecutionLease(path,clock=lambda:AT)
    assert not old.db_path.exists()


def test_window_latency_excludes_old_timeout_and_disconnected_research(pilot,environment):
    from app.asset_modules.grok_latency_report import summarize
    root,old,_=pilot
    first=old.reserve_request('MASTER',{'mode':'PAPER'})
    old.finish_request(first,dict(status='FAILED_OR_REVOKED',latency_ms=15000))
    path,_=prepare_next_window(root,root,clock=lambda:AT)
    lease=ExecutionLease(path,clock=lambda:AT)
    actual=lease.reserve_request('MASTER',{'mode':'PAPER'})
    lease.finish_request(actual,dict(status='ANSWER_RECEIVED',latency_ms=5000))
    research=lease.reserve_request('LEARNING',{'mode':'PAPER'})
    lease.finish_request(research,dict(status='ANSWER_RECEIVED',latency_ms=100))
    report=summarize(root,window=path)
    assert report['requests_in_this_ledger']==3 and report['requests_in_report']==1
    assert report['requests_excluded_by_window']==2
    assert report['latency_ms']['median']==report['latency_ms']['max']==5000
    assert report['requests_by_role']=={'MASTER':1}
    assert summarize(root)['latency_ms']['max']==15000
    assert '--window' in (root/('Show-Grok-Latency-'+lease.doc['id']+'.ps1')).read_text()


def test_retained_prior_intention_offset_is_not_reset_or_double_counted(tmp_path,environment):
    from app.asset_modules.grok_execution_trial import create
    from app.equity_overhaul.paper_experiment import create as parent,load,PaperExperiment
    root=tmp_path/'trial';parent(root,lambda:NOW,profile='CENT_EARLY_V2')
    path,spec=load(root/'experiment.json',lambda:NOW);PaperExperiment(path,spec,clock=lambda:NOW)
    previous=root/'grok-order-intentions';previous.mkdir()
    (previous/'summary.json').write_text(json.dumps({'requests_scheduled':3,'provider_requests_attempted':3}))
    base,_=create(root,clock=lambda:NOW);old=ExecutionLease(base,clock=lambda:NOW)
    old.reserve_request('MASTER',{'mode':'PAPER'})
    window,_=prepare_next_window(root,root,clock=lambda:AT)
    lease=ExecutionLease(window,clock=lambda:AT)
    assert lease.doc['prior_paid_attempts']==3 and lease.request_count()==4
    lease.reserve_request('CRYPTO',{'mode':'PAPER'})
    assert old.request_count()==lease.request_count()==5

"""Disconnected tests: fake source bytes, fake database, fake model transport."""
from datetime import UTC,datetime
from importlib.util import spec_from_file_location,module_from_spec
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace,ModuleType

import pytest

from app.strategy_lab import prepare as prep
from app.strategy_lab import supervisor as manager
from app.asset_modules.grok import GrokUnavailable

NOW=1800000000.0


@pytest.fixture
def lab(tmp_path,monkeypatch):
    upstream=tmp_path/'source';upstream.mkdir()
    (upstream/prep.LOCK['strategy_file']).write_text('# synthetic source, not NFI\n')
    (upstream/'LICENSE').write_text('synthetic license\n')
    lock={**prep.LOCK,'strategy_sha256':prep.digest(upstream/prep.LOCK['strategy_file']),
          'license_sha256':prep.digest(upstream/'LICENSE')}
    monkeypatch.setattr(prep,'LOCK',lock);monkeypatch.setattr(manager,'LOCK',lock)
    root=tmp_path/'lab';manifest=prep.prepare(root,upstream)
    return root,manifest,upstream


def make_db(root,rows=()):
    with sqlite3.connect(root/'user_data/lab-trades.sqlite') as db:
        db.execute('CREATE TABLE trades(strategy TEXT,is_open INTEGER,close_profit_abs REAL,stake_amount REAL)')
        db.executemany('INSERT INTO trades VALUES(?,?,?,?)',rows)


class Client:
    def __init__(self,answer=None,fail=False,callback=None):
        self.calls=[];self.answer=answer or {'action':'ALLOW','reason':'Bounded virtual experiment'}
        self.fail=fail;self.callback=callback
    def request(self,*args):
        self.calls.append(args)
        if self.callback:self.callback()
        if self.fail:raise GrokUnavailable('FAILED')
        return self.answer


@pytest.fixture
def wrapper(monkeypatch):
    upstream=ModuleType('NostalgiaForInfinityX8')
    class Base:
        def __init__(self,config):self.config=config;self.entry_calls=0
        def confirm_trade_entry(self,*args,**kwargs):self.entry_calls+=1;return True
        def confirm_trade_exit(self,*args,**kwargs):raise AssertionError('Exit veto must not be consulted')
    upstream.NostalgiaForInfinityX8=Base
    monkeypatch.setitem(sys.modules,'NostalgiaForInfinityX8',upstream)
    spec=spec_from_file_location('lab_test_wrapper',prep.MODULE/'AtlasNFILab.py')
    module=module_from_spec(spec);spec.loader.exec_module(module);return module


def test_preparation_is_paused_and_separate_no_secrets(lab):
    root,m,source=lab
    assert manager.verify(root)==m
    c=json.loads((root/'user_data/config.json').read_text())
    compose=json.loads((root/'compose.json').read_text())
    service=compose['services']['atlas-nfi-lab']
    assert '@sha256:' in service['image'] and service['restart']=='no'
    assert not {'ports','env_file','environment','privileged'}&set(service)
    assert c['dry_run'] and c['max_open_trades']==2 and c['stoploss']==-.02
    assert not c['exchange']['key'] and not c['exchange']['secret']
    assert json.loads((root/'user_data/grok-control.json').read_text())['action']=='PAUSE'
    assert not m['upstream_performance_applies']
    with pytest.raises(FileExistsError):prep.prepare(root,source)


def test_changed_upstream_refused_before_runnable_output(lab,tmp_path):
    root,m,source=lab;(source/prep.LOCK['strategy_file']).write_text('changed')
    output=tmp_path/'refused'
    with pytest.raises(ValueError,match='PINNED_NFI_SOURCE_MISMATCH'):prep.prepare(output,source)
    assert not output.exists()


@pytest.mark.parametrize('name',['compose.json','user_data/config.json','user_data/strategies/AtlasNFILab.py'])
def test_changed_prepared_files_revoke_authority(lab,name):
    root,m,_=lab;(root/name).write_text('changed')
    with pytest.raises(ValueError,match='CHANGED'):manager.verify(root)


def test_manager_waits_for_real_native_database_without_calling_model(lab):
    root,m,_=lab;c=Client();r=manager.cycle(root,c,clock=lambda:NOW)
    assert not c.calls and r['status']=='WAITING_FOR_NATIVE_BOT'
    assert r['action']=='PAUSE'


def test_ledger_is_readonly_and_only_lab_rows_sent(lab):
    root,m,_=lab
    make_db(root,[('AtlasNFILab',0,2,50),('AtlasNFILab',0,-1,50),('AtlasNFILab',1,None,50),('OTHER',0,999,1000)])
    db=root/'user_data/lab-trades.sqlite';before=db.read_bytes()
    d=manager.evidence(root)
    assert d['realized_net']==1 and d['closed_trades']==2 and d['open_positions']==1
    assert d['open_stake']==50 and d['unrealized_pnl']=='UNKNOWN'
    assert db.read_bytes()==before


def test_valid_manager_leases_native_entries_only(lab,wrapper):
    root,m,_=lab;make_db(root)
    c=Client();r=manager.cycle(root,c,clock=lambda:NOW)
    control=root/'user_data/grok-control.json'
    assert r['status']=='MANAGED' and len(c.calls)==1 and r['orders']==0
    assert wrapper.entry_lease(control,m['lab_id'],NOW+1)
    assert not wrapper.entry_lease(control,m['lab_id'],NOW+601)
    assert c.calls[0][2]['mode']=='PAPER'
    assert set(c.calls[0][2])=={'mode','asset','experiment','evidence','limits'}


@pytest.mark.parametrize('answer',[
    {'action':'BUY','reason':'invalid'}, {'action':'ALLOW','reason':'ok','price':100},
    {'action':'ALLOW','reason':''}, {'action':'ALLOW','reason':False}, []])
def test_manager_cannot_add_trade_commands_or_malformed_decisions(lab,answer):
    root,m,_=lab;make_db(root);c=Client();c.answer=answer
    r=manager.cycle(root,c,clock=lambda:NOW)
    assert r['status']=='MANAGER_UNAVAILABLE_OR_REVOKED'
    assert json.loads((root/'user_data/grok-control.json').read_text())['action']=='PAUSE'


@pytest.mark.parametrize('mode',['provider-failed','late','config-changed','loss-during-request'])
def test_failed_or_revoked_completion_leaves_entries_paused(lab,mode):
    root,m,_=lab;make_db(root);c=Client(fail=mode=='provider-failed')
    if mode=='config-changed':c.callback=lambda:(root/'compose.json').write_text('changed')
    if mode=='loss-during-request':
        def lose():
            with sqlite3.connect(root/'user_data/lab-trades.sqlite') as db:
                db.execute("INSERT INTO trades VALUES('AtlasNFILab',0,-21,50)")
        c.callback=lose
    times=iter([NOW,NOW+46 if mode=='late' else NOW+1])
    r=manager.cycle(root,c,clock=lambda:next(times))
    assert r['action']=='PAUSE'
    assert json.loads((root/'user_data/grok-control.json').read_text())['action']=='PAUSE'


@pytest.mark.parametrize('rows',[
    [('AtlasNFILab',0,-20,50)], [('AtlasNFILab',1,None,101)],
    [('AtlasNFILab',1,None,25)]*3])
def test_loss_and_exposure_limits_prevent_model_calls(lab,rows):
    root,m,_=lab;make_db(root,rows);c=Client()
    assert manager.cycle(root,c,clock=lambda:NOW)['status']=='NATIVE_EXPERIMENT_LOSS_OR_EXPOSURE_LIMIT'
    assert not c.calls


@pytest.mark.parametrize('change',[
    {'dry_run':False},{'trading_mode':'futures'},{'stoploss':-.99},
    {'position_adjustment_enable':True},{'force_entry_enable':True},
    {'api_server':{'enabled':True}}, {'exchange':{'name':'kraken','key':'secret'}}])
def test_strategy_rejects_live_or_broadened_config(wrapper,change):
    cfg=prep.config('a'*32);cfg.update(change)
    with pytest.raises(ValueError,match='ISOLATED_DRY_RUN'):wrapper.AtlasNFILab(cfg)


def test_lease_controls_entries_but_never_exits_or_adds_size(lab,wrapper):
    root,m,_=lab;cfg=prep.config(m['lab_id']);cfg['user_data_dir']=root/'user_data'
    strategy=wrapper.AtlasNFILab(cfg);now=datetime.fromtimestamp(NOW,UTC)
    args=('BTC/USD','limit',.001,100,'GTC',now,'1','long')
    assert not strategy.confirm_trade_entry(*args) and strategy.entry_calls==0
    manager.write_control(root,m['lab_id'],'ALLOW',NOW)
    assert strategy.confirm_trade_entry(*args) and strategy.entry_calls==1
    manager.write_control(root,m['lab_id'],'PAUSE',NOW)
    assert not strategy.confirm_trade_entry(*args)
    assert strategy.confirm_trade_exit(None,None,None,None,None,None,'stop_loss',now)
    assert strategy.adjust_trade_position() is None
    assert strategy.custom_stake_amount('BTC/USD',now,100,200,10,500,1,'1','long')==50
    assert strategy.custom_stake_amount('BTC/USD',now,100,200,60,500,1,'1','long')==0
    assert strategy.custom_stake_amount('BTC/USD',now,100,200,10,500,2,'1','long')==0


@pytest.mark.parametrize('mode',['expired','future','foreign','extra-field','too-long','bad-type'])
def test_bad_lease_fails_closed(lab,wrapper,mode):
    root,m,_=lab;manager.write_control(root,m['lab_id'],'ALLOW',NOW)
    p=root/'user_data/grok-control.json';d=json.loads(p.read_text())
    if mode=='expired':d['expires_at']=NOW
    elif mode=='future':d['issued_at']=NOW+1
    elif mode=='foreign':d['lab_id']='b'*32
    elif mode=='extra-field':d['price']=100
    elif mode=='too-long':d['expires_at']=NOW+601
    else:d['issued_at']=True
    p.write_text(json.dumps(d))
    assert not wrapper.entry_lease(p,m['lab_id'],NOW)


def test_manual_pause_cannot_be_overridden_by_inflight_or_next_model(lab,wrapper):
    root,m,_=lab;make_db(root)
    def pause():
        (root/'user_data/PAUSE_REQUESTED').touch()
    c=Client(callback=pause)
    r=manager.cycle(root,c,clock=lambda:NOW)
    assert r['status']=='MANUAL_PAUSE' and r['action']=='PAUSE'
    assert not wrapper.entry_lease(root/'user_data/grok-control.json',m['lab_id'],NOW)
    c=Client()
    assert manager.cycle(root,c,clock=lambda:NOW)['status']=='MANUAL_PAUSE' and not c.calls


def test_flat_ledger_reports_zero_unrealized_exposure(lab):
    root,m,_=lab;make_db(root)
    d=manager.evidence(root)
    assert d['open_positions']==0 and d['open_stake']==0 and d['unrealized_pnl']==0
    assert d['total_trades']==d['unreconciled_closed_trades']==0


class BootstrapClock:
    def __init__(self):self.elapsed=0;self.after_wait=None
    def wall(self):return NOW+self.elapsed
    def mono(self):return self.elapsed
    def wait(self,seconds):
        self.elapsed+=seconds
        if self.after_wait:self.after_wait()


def test_empty_sample_collects_under_native_lease_without_any_model_call(lab,wrapper,monkeypatch):
    from app.strategy_lab import bootstrap
    root,m,_=lab;make_db(root);clock=BootstrapClock();seen=[]
    monkeypatch.setattr(manager,'configured_client',lambda *a,**k:pytest.fail('No model for bootstrap'))
    def native_trial():
        seen.append(wrapper.entry_lease(root/'user_data/grok-control.json',m['lab_id'],clock.wall()))
        with sqlite3.connect(root/'user_data/lab-trades.sqlite') as db:
            db.executemany('INSERT INTO trades VALUES(?,?,?,?)',[('AtlasNFILab',0,p,50) for p in (1,-1,0)])
    clock.after_wait=native_trial
    r=bootstrap.run(root,clock=clock.wall,timer=clock.mono,wait=clock.wait,progress=None)
    assert seen==[True] and r['status']=='INITIAL_SAMPLE_COLLECTED'
    assert r['grok_requests']==0 and r['final_evidence']['closed_trades']==3
    assert not wrapper.entry_lease(root/'user_data/grok-control.json',m['lab_id'],clock.wall())
    assert not (root/'manager.lock').exists()


@pytest.mark.parametrize('mode',['timeout','manual-pause','loss','source-change','clock-jump','interrupt'])
def test_bootstrap_revokes_lease_on_all_stop_paths(lab,mode):
    from app.strategy_lab import bootstrap
    root,m,_=lab;make_db(root);clock=BootstrapClock()
    def event():
        if mode=='manual-pause':(root/'user_data/PAUSE_REQUESTED').touch()
        elif mode=='loss':
            with sqlite3.connect(root/'user_data/lab-trades.sqlite') as db:
                db.execute("INSERT INTO trades VALUES('AtlasNFILab',0,-20,50)")
        elif mode=='source-change':(root/'compose.json').write_text('changed')
        elif mode=='interrupt':raise KeyboardInterrupt
    clock.after_wait=event
    r=bootstrap.run(root,clock=(lambda:clock.wall()+3 if clock.elapsed else NOW) if mode=='clock-jump' else clock.wall,
        timer=clock.mono,wait=clock.wait,progress=None)
    assert r['status']=={'timeout':'TIME_LIMIT','manual-pause':'MANUAL_PAUSE','loss':'REALIZED_LOSS_LIMIT',
        'source-change':'CHECK_FAILED_OR_REVOKED','clock-jump':'CLOCK_DISCONTINUITY','interrupt':'OPERATOR_STOP'}[mode]
    assert r['elapsed_seconds']<=3600 and r['entries_paused']
    assert json.loads((root/'user_data/grok-control.json').read_text())['action']=='PAUSE'
    assert not (root/'manager.lock').exists()


@pytest.mark.parametrize('rows,expected',[
    ([('AtlasNFILab',1,None,50)],'FLAT_START_REQUIRED'),
    ([('AtlasNFILab',0,1,50)]*3,'SAMPLE_ALREADY_AVAILABLE'),
    ([('AtlasNFILab',0,None,50)],'UNRECONCILED_NATIVE_LEDGER'),
    ([('AtlasNFILab',1,None,101)],'EXPOSURE_LIMIT'),
])
def test_bootstrap_refuses_unready_start_without_widening_permissions(lab,rows,expected):
    from app.strategy_lab import bootstrap
    root,m,_=lab;make_db(root,rows);clock=BootstrapClock()
    r=bootstrap.run(root,clock=clock.wall,timer=clock.mono,wait=lambda _:pytest.fail('Must not wait'),progress=None)
    assert r['status']==expected and r['entries_paused']


def test_bootstrap_uses_shared_manager_lock_and_requires_explicit_restart_after_manual_pause(lab):
    from app.strategy_lab import bootstrap
    root,m,_=lab;make_db(root);clock=BootstrapClock();lock=root/'manager.lock';lock.write_text('other')
    with pytest.raises(ValueError,match='MANAGER_ALREADY_RUNNING'):
        bootstrap.run(root,clock=clock.wall,timer=clock.mono,wait=clock.wait,progress=None)
    assert lock.read_text()=='other';lock.unlink()
    (root/'user_data/PAUSE_REQUESTED').touch()
    r=bootstrap.run(root,clock=clock.wall,timer=clock.mono,wait=clock.wait,progress=None)
    assert r['status']=='MANUAL_PAUSE'
    clock.after_wait=lambda:(root/'user_data/PAUSE_REQUESTED').touch()
    r=bootstrap.run(root,new_trial=True,clock=clock.wall,timer=clock.mono,wait=clock.wait,progress=None)
    assert r['elapsed_seconds']==30 and r['status']=='MANUAL_PAUSE'


def test_bootstrap_renewal_never_exceeds_short_lease_or_deadline(lab):
    from app.strategy_lab import bootstrap
    root,m,_=lab;make_db(root);clock=BootstrapClock();leases=[]
    def observe():
        control=json.loads((root/'user_data/grok-control.json').read_text())
        leases.append(control)
    clock.after_wait=observe
    bootstrap.run(root,clock=clock.wall,timer=clock.mono,wait=clock.wait,progress=None)
    assert leases and all(0<d['expires_at']-d['issued_at']<=90 for d in leases)
    assert max(d['expires_at'] for d in leases)<=NOW+3600

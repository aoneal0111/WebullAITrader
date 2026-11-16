import json
from pathlib import Path
from types import SimpleNamespace as NS
import errno

from app.strategy_lab.crypto_status_io import publish_status, STATUS_ERROR, RETRY_DELAYS


def test_exact_reported_permission_error_retries_and_preserves_complete_json(tmp_path):
    path=tmp_path/'engine-status.json';path.write_text('{"updated_at":"old"}')
    evidence=NS(error=None);calls=[];delays=[]
    def replace(source,target):
        calls.append(source)
        assert json.loads(path.read_text())=={'updated_at':'old'}
        if len(calls)<3:raise PermissionError(errno.EACCES,'Permission denied',str(source),str(target))
        source.replace(target)
    assert publish_status(path,{'updated_at':'new','state':'RUNNING'},evidence,replace=replace,sleep=delays.append)
    assert len(calls)==3 and delays==list(RETRY_DELAYS[:2])
    assert evidence.error is None and json.loads(path.read_text())['updated_at']=='new'
    assert not list(tmp_path.glob('*.tmp'))


def test_persistent_failure_gates_entries_but_does_not_throw_or_fake_freshness(tmp_path):
    path=tmp_path/'engine-status.json';old='{"updated_at":"old","state":"RUNNING"}';path.write_text(old)
    evidence=NS(error=None);attempts=[]
    def denied(source,target):
        attempts.append(1);raise PermissionError(errno.EACCES,'Permission denied',str(source),str(target))
    status={'updated_at':'new','state':'RUNNING'}
    assert not publish_status(path,status,evidence,replace=denied,sleep=lambda _:None)
    assert evidence.error==STATUS_ERROR and status['status_publication']['state']=='FAILED'
    assert path.read_text()==old and len(attempts)==len(RETRY_DELAYS)+1
    assert not list(tmp_path.glob('*.tmp'))
    assert publish_status(path,status,evidence,sleep=lambda _:None)
    assert evidence.error is None and json.loads(path.read_text())['capture_error'] is None


def test_unrelated_evidence_failure_is_never_cleared(tmp_path):
    evidence=NS(error='CAPTURE_SUBMIT_FAILED')
    assert publish_status(tmp_path/'engine-status.json',{},evidence)
    assert evidence.error=='CAPTURE_SUBMIT_FAILED'


def test_other_io_failure_is_not_retried_and_keeps_original_status(tmp_path):
    path=tmp_path/'engine-status.json';path.write_text('{}');evidence=NS(error=None);calls=[]
    def full(source,target):calls.append(1);raise OSError(errno.ENOSPC,'No space left')
    assert not publish_status(path,{},evidence,replace=full,sleep=lambda _:None)
    assert len(calls)==1 and evidence.error==STATUS_ERROR and path.read_text()=='{}'


def test_worker_keeps_native_exit_cycles_running_and_resumes_entries_after_recovery(tmp_path,monkeypatch):
    import sys
    from enum import StrEnum
    from types import ModuleType, MethodType
    from app.strategy_lab import crypto_fleet_runtime as runtime, crypto_status_io as io
    from app.strategy_lab import nfi_forward_engine as native, crypto_workspace_telemetry as telemetry, kraken_public_transport as transport
    class State(StrEnum):
        RUNNING='running';PAUSED='paused';STOPPED='stopped'
    actions=[];cycle=[0]
    bot=NS(config={'dry_run':True},active_pair_whitelist=[],
        exchange=NS(_api=NS(),_api_async=NS()),
        execute_entry=lambda *a,**k:actions.append(('entry',cycle[0])) or True,
        execute_trade_exit=lambda:actions.append(('exit',cycle[0])))
    journal=NS(status=lambda:dict(writer_alive=True,evidence_complete=True,closed=False),close=lambda:None)
    evidence=NS(error=None,root=tmp_path/'recording',journal=journal,complete_cycle=True,
        ready=lambda *a:True,emit=lambda *a:True,entry_reviews={},source_states={},signal_snapshots=0,buy_signal_snapshots=0,
        capture=lambda *a:None,prefix='TEST',_response_hashes={})
    evidence.healthy=MethodType(native.Evidence.healthy,evidence)
    evidence.attach=MethodType(native.Evidence.attach,evidence)
    class Worker:
        def __init__(self,*a,**kw):self.freqtrade=bot
        def _worker(self,old_state):
            cycle[0]+=1;bot.execute_entry('BTC/USD',100);bot.execute_trade_exit()
            return State.STOPPED if cycle[0]==3 else State.RUNNING
        def exit(self):pass
    worker_module=ModuleType('freqtrade.worker');worker_module.Worker=Worker
    enum_module=ModuleType('freqtrade.enums');enum_module.State=State
    monkeypatch.setitem(sys.modules,'freqtrade.worker',worker_module);monkeypatch.setitem(sys.modules,'freqtrade.enums',enum_module)
    monkeypatch.setattr(runtime.signal,'signal',lambda *a:None)
    monkeypatch.setattr(runtime,'resolve',lambda work:([],{},None,dict(key='boll',timeframe='4h')))
    monkeypatch.setattr(runtime,'Evidence',lambda *a,**kw:evidence)
    monkeypatch.setattr(runtime,'digest',lambda p:'a'*64)
    monkeypatch.setattr(runtime,'pace_public_requests',lambda *a:NS())
    monkeypatch.setattr(runtime,'capture_native_actions',lambda *a:None)
    monkeypatch.setattr(runtime,'ledger_status',lambda *a,**kw:{})
    monkeypatch.setattr(transport,'monitor',lambda *a:None)
    monkeypatch.setattr(telemetry,'publish',lambda *a:None)
    original=io.publish_status;writes=[0]
    def publish(path,status,ev):
        writes[0]+=1
        def replace(source,target):
            if writes[0]==2:raise PermissionError(errno.EACCES,'Permission denied',str(source),str(target))
            source.replace(target)
        return original(path,status,ev,replace=replace,sleep=lambda _:None)
    monkeypatch.setattr(io,'publish_status',publish)
    runtime.run(tmp_path,tmp_path/'shared')
    assert [n for kind,n in actions if kind=='exit']==[1,2,3]
    assert [n for kind,n in actions if kind=='entry']==[1,3]
    assert evidence.error is None

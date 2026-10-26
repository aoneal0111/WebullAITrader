from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import pytest
from app.asset_modules.grok import GrokClient
from app.asset_modules.grok_online_observer import run
from app.asset_modules.grok_observer_capture import _encode
from tests.asset_modules.test_grok_observer import NOW,BASE,snap
from tests.asset_modules.test_grok_observer_context import evidence


def native_line(*,source='WEBULL_OPENAPI',engine=None):
    s=replace(snap() if engine is None else snap(engine,event_id='event-'+engine.name),source=source)
    e=replace(evidence(s),source=source)
    return _encode(s,e,BASE,NOW,'WEBULL_PROVIDER_QUOTE_TIME_SHARED' if source=='WEBULL_OPENAPI' else 'FIXTURE_ONLY')


def client(calls):
    def transport(request):
        payload=json.loads(request.data);calls.append(payload)
        return json.dumps(dict(status='completed',usage=dict(input_tokens=100,output_tokens=20),output=[dict(type='message',role='assistant',status='completed',content=[dict(type='output_text',text=json.dumps(dict(view='PASS',reason='Observation only')))])])).encode()
    return GrokClient('local-fixture','grok-test',transport=transport,clock=lambda:0)


def test_separate_live_reader_sends_one_fresh_context_and_records_usage(tmp_path):
    source=tmp_path/'capture.jsonl';source.write_bytes(native_line())
    timer=[0.0];calls=[]
    def wait(_):timer[0]+=60
    r=run(source,BASE,tmp_path/'out',client(calls),clock=lambda:NOW+timedelta(seconds=timer[0]),timer=lambda:timer[0],wait=wait)
    assert r['inference_attempts']==1 and len(calls)==1 and r['statuses']=={'OBSERVED':1}
    assert r['usage']['input_tokens']==100 and r['usage']['output_tokens']==20
    assert not r['execution_authorized'] and r['orders']==0 and not r['baseline_modified']
    assert 'tools' not in calls[0] and not calls[0]['store']
    assert r['stop_reason']=='TIME_LIMIT' and (tmp_path/'out/summary.json').is_file()


@pytest.mark.parametrize('case',['stale','malformed','fixture','wrong_baseline'])
def test_unusable_input_never_makes_paid_request(tmp_path,case):
    source=tmp_path/'capture.jsonl';source.write_bytes(native_line(source='RECORDED_FIXTURE' if case=='fixture' else 'WEBULL_OPENAPI'))
    if case=='malformed':source.write_bytes(b'{"bad":true}\n')
    timer=[0.0];calls=[]
    r=run(source,'b'*64 if case=='wrong_baseline' else BASE,tmp_path/'out',client(calls),
        clock=lambda:NOW+timedelta(seconds=10),timer=lambda:timer[0],wait=lambda _:timer.__setitem__(0,timer[0]+300))
    assert not calls and r['inference_attempts']==0 and r['orders']==0


def test_partial_append_is_ignored_until_complete_without_retiming(tmp_path):
    source=tmp_path/'capture.jsonl';line=native_line();source.write_bytes(line[:-5])
    timer=[0.0];calls=[]
    def wait(_):
        source.write_bytes(line)
        timer[0]+=.1 if timer[0]<.1 else 300
    r=run(source,BASE,tmp_path/'out',client(calls),clock=lambda:NOW,timer=lambda:timer[0],wait=wait)
    assert r['inference_attempts']==1 and len(calls)==1


def test_cli_requires_online_opt_in_before_client_or_output(tmp_path,monkeypatch):
    from app.asset_modules.grok_online_observer import main
    monkeypatch.setattr('sys.argv',['check','--repo',str(tmp_path),'--capture',str(tmp_path/'in'),
        '--baseline',BASE,'--output',str(tmp_path/'out')])
    with pytest.raises(SystemExit) as e:main()
    assert e.value.code==2 and not (tmp_path/'out').exists()


def test_missing_usage_is_reported_without_assuming_free_request():
    calls=[];c=client(calls)
    c._transport=lambda request:json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',status='completed',content=[dict(type='output_text',text='{"ok":true}')])])).encode()
    c.request('MASTER','fixture',{'mode':'PAPER'},{})
    assert c.usage()['responses_without_usage']==1
    assert c.usage()['responses_with_usage']==0



def test_capture_rewrite_is_detected_before_any_second_inference(tmp_path):
    source=tmp_path/'capture.jsonl';line=native_line();source.write_bytes(line)
    timer=[0.0];calls=[]
    def wait(_):
        source.write_bytes(line.replace(b'event1',b'event2'))
        timer[0]+=60
    r=run(source,BASE,tmp_path/'out',client(calls),clock=lambda:NOW,
        timer=lambda:timer[0],wait=wait)
    assert r['stop_reason']=='CAPTURE_REVISED' and len(calls)==1


def test_failed_transport_is_not_retried_and_unknown_cost_is_visible(tmp_path):
    source=tmp_path/'capture.jsonl';source.write_bytes(native_line())
    timer=[0.0];calls=[];c=client(calls)
    def fail(request):raise OSError('SECRET_PROVIDER_BODY')
    c._transport=fail
    r=run(source,BASE,tmp_path/'out',c,clock=lambda:NOW,timer=lambda:timer[0],
        wait=lambda _:timer.__setitem__(0,timer[0]+60))
    assert r['inference_attempts']==1 and r['statuses']=={'PROVIDER_UNAVAILABLE':1}
    assert r['usage']['attempts_without_reported_usage']==1
    assert 'SECRET' not in str(r)



def test_both_owners_are_observed_through_real_serializer_without_crossing_roles(tmp_path):
    from app.asset_modules.engine_catalog import EngineId
    source=tmp_path/'capture.jsonl'
    source.write_bytes(native_line(engine=EngineId.WARRIOR)+native_line(engine=EngineId.SCALPER))
    timer=[0.0];calls=[]
    r=run(source,BASE,tmp_path/'out',client(calls),clock=lambda:NOW,timer=lambda:timer[0],
        wait=lambda _:timer.__setitem__(0,timer[0]+60))
    assert r['inference_attempts']==2 and r['statuses']['OBSERVED']==2
    assert set(r['records_by_engine'])=={'WARRIOR_MOMENTUM_V1','QUICK_SCALPER'}
    assert [json.loads(p['input'][1]['content'])['engine'] for p in calls]==['WARRIOR_MOMENTUM_V1','QUICK_SCALPER']
    assert not r['execution_authorized']


def test_subprocess_observer_uses_fake_transport_and_never_networks(tmp_path):
    import subprocess,sys
    source=tmp_path/'capture.jsonl';source.write_bytes(native_line())
    code="""
import socket,sys
from pathlib import Path
from tests.asset_modules.test_grok_online_observer import client,NOW,BASE
from app.asset_modules.grok_online_observer import run
socket.socket=lambda *a,**k:(_ for _ in ()).throw(AssertionError('Network forbidden'))
t=[0.];calls=[]
r=run(Path(sys.argv[1]),BASE,Path(sys.argv[2]),client(calls),clock=lambda:NOW,timer=lambda:t[0],wait=lambda _:t.__setitem__(0,t[0]+300))
assert r['orders']==0 and len(calls)==1 and r['stop_reason']=='TIME_LIMIT'
"""
    result=subprocess.run([sys.executable,'-c',code,str(source),str(tmp_path/'out')],
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads((tmp_path/'out/summary.json').read_text())['inference_attempts']==1


@pytest.mark.parametrize('case',['bad_scope','wrong_capture','wrong_hash'])
def test_manifest_setup_rejects_unbound_input_before_constructing_provider(tmp_path,monkeypatch,case):
    from app.asset_modules.grok_online_observer import main
    manifest=tmp_path/'observer-manifest.json'
    data=dict(scope='NATIVE_OBSERVER_SESSION_MANIFEST_V1',baseline_sha256=BASE,
        source_sha256='a'*64,settings_sha256='b'*64)
    if case=='bad_scope':data['scope']='UNTRUSTED'
    if case=='wrong_hash':data['baseline_sha256']='invalid'
    manifest.write_text(json.dumps(data))
    capture=tmp_path/'other.jsonl'
    monkeypatch.setenv('WEBULL_TRADING_ENVIRONMENT','PAPER')
    monkeypatch.setenv('TRADING_ENVIRONMENT','PAPER')
    monkeypatch.setenv('LIVE_TRADING_ENABLED','false')
    monkeypatch.setenv('ATLAS_GROK_PAPER_ENTRIES_ENABLED','false')
    monkeypatch.setattr('app.asset_modules.grok.configured_client',lambda _:pytest.fail('Provider constructed'))
    monkeypatch.setattr('sys.argv',['check','--repo',str(tmp_path),'--capture',str(capture),
        '--baseline-manifest',str(manifest),'--output',str(tmp_path/'out'),'--online'])
    assert main()==2 and not (tmp_path/'out').exists()

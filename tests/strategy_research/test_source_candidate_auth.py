import json
import pytest
from app.strategy_research.selected_sip_capture import credential_candidates,credentials_from_local,CaptureError
from app.strategy_research.source_candidate_capture import capture


def test_partial_environment_cannot_override_complete_dotenv_pair(tmp_path):
    (tmp_path/'.env').write_text('ALPACA_API_KEY=file-key\nALPACA_API_SECRET=file-secret\n')
    assert credential_candidates(tmp_path,environment={'ALPACA_API_KEY':'stale-process-key'})==[
        ('DOTENV_ALPACA',('file-key','file-secret'))]


def test_namespaces_are_not_mixed_and_equal_pairs_are_deduplicated(tmp_path):
    (tmp_path/'.env').write_text('ALPACA_API_KEY=same-key\nALPACA_API_SECRET=same-secret\n')
    assert credential_candidates(tmp_path,environment={'ALPACA_API_KEY':'same-key','ALPACA_API_SECRET':'same-secret',
        'APCA_API_KEY_ID':'same-key','APCA_API_SECRET_KEY':'same-secret'})==[('PROCESS_ALPACA',('same-key','same-secret'))]
    assert credential_candidates(tmp_path/'absent',environment={'ALPACA_API_KEY':'key','APCA_API_SECRET_KEY':'secret'})==[]


def test_401_tries_next_complete_pair_then_keeps_selected_pair(tmp_path,monkeypatch):
    import app.strategy_research.source_candidate_capture as module
    candidates=[('PROCESS_ALPACA',('bad-key','bad-secret')),('DOTENV_ALPACA',('good-key','good-secret'))]
    monkeypatch.setattr(module,'credential_candidates',lambda _:candidates)
    calls=[]
    def fetch(params,pair):
        calls.append((params,pair))
        if pair==candidates[0][1]:raise CaptureError('HTTP_401')
        # A later schema failure preserves proof the second identity was accepted.
        return json.dumps({'bars':{'BAD':[]}}).encode()
    out=tmp_path/'out'
    with pytest.raises(CaptureError,match='UNREQUESTED'):capture(tmp_path,out,fetch=fetch)
    assert [p for _,p in calls]==[candidates[0][1],candidates[1][1]]
    report=json.loads((out/'capture-summary.json').read_text())
    assert report['credential_source']=='DOTENV_ALPACA'
    assert report['authentication_checks']==[{'source':'PROCESS_ALPACA','status':'HTTP_401'},
                                            {'source':'DOTENV_ALPACA','status':'REQUEST_ACCEPTED'}]
    assert 'good-secret' not in str(report) and 'bad-key' not in str(report)


@pytest.mark.parametrize('status',['HTTP_403','HTTP_429','NETWORK_ERROR'])
def test_other_failures_do_not_try_different_credentials(tmp_path,monkeypatch,status):
    import app.strategy_research.source_candidate_capture as module
    monkeypatch.setattr(module,'credential_candidates',lambda _:[('PROCESS_ALPACA',('one','secret')),('DOTENV_ALPACA',('two','secret'))])
    calls=[]
    def fetch(*args):
        calls.append(args)
        raise CaptureError(status)
    with pytest.raises(CaptureError,match=status):capture(tmp_path,tmp_path/'out',fetch=fetch)
    assert len(calls)==1


def test_all_401s_are_bounded_and_reported_without_pair_values(tmp_path,monkeypatch):
    import app.strategy_research.source_candidate_capture as module
    monkeypatch.setattr(module,'credential_candidates',lambda _:[('PROCESS_ALPACA',('fixture-key','fixture-secret'))])
    with pytest.raises(CaptureError,match='ALL_COMPLETE_LOCAL_PAIRS_HTTP_401'):
        capture(tmp_path,tmp_path/'out',fetch=lambda *_:(_ for _ in ()).throw(CaptureError('HTTP_401')))
    report=json.loads((tmp_path/'out/capture-summary.json').read_text())
    assert report['attempts']==1 and 'fixture-key' not in str(report)


def test_successful_capture_reuses_accepted_pair_for_later_pages(tmp_path,monkeypatch):
    from datetime import date,timedelta
    import app.strategy_research.source_candidate_capture as module
    candidates=[('PROCESS_ALPACA',('bad','secret')),('DOTENV_APCA',('accepted','secret'))]
    monkeypatch.setattr(module,'credential_candidates',lambda _:candidates)
    days=[(date(2014,1,1)+timedelta(days=i)).isoformat() for i in range(250)]
    def bars(selected):return [dict(t=d+'T05:00:00Z',o=10,h=11,l=9,c=10,v=100) for d in selected]
    calls=[]
    def fetch(params,pair):
        calls.append(pair)
        if pair[0]=='bad':raise CaptureError('HTTP_401')
        if not params.get('page_token'):
            return json.dumps(dict(bars={'SPY':bars(days[:125])},next_page_token='second')).encode()
        return json.dumps(dict(bars={s:bars(days[125:] if s=='SPY' else days) for s in module.SYMBOLS},next_page_token=None)).encode()
    report=capture(tmp_path,tmp_path/'out',fetch=fetch)
    assert calls==[candidates[0][1],candidates[1][1],candidates[1][1]]
    assert report['status']=='COMPLETE' and report['rows']==250*31
    assert report['attempts']==3 and len(report['authentication_checks'])==2

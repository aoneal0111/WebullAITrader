from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.asset_modules.grok_engine_teams import EngineTeams, TeamBudget, review_team
from app.asset_modules.grok import GrokUnavailable
from app.equity_overhaul.paper_experiment import create

NOW = datetime(2026, 11, 3, 14, tzinfo=UTC)


@pytest.fixture
def trial(tmp_path):
    root = tmp_path / 'trial'
    create(root, clock=lambda: NOW, profile='CENT_EXECUTABLE_V3')
    return root


def answer(action='PASS'):
    return dict(action=action, entry='10', stop='9.95', target='10.10', reason='Synthetic research')


def setup(trial):
    made = []
    def factory(*args, **kwargs):
        c = Mock(); c.request.return_value=answer();c.usage.return_value={};c.diagnostics.return_value={}
        made.append(c); return c
    teams = EngineTeams({'XAI_API_KEY':'fixture-only','ATLAS_GROK_MODEL':'grok-4.7'},trial,
        clock=lambda:NOW,factory=factory)
    # No background HTTP in tests; news worker is tested separately.
    teams.offer_symbol=lambda _:None
    return teams, made


def snapshot(engine='QUICK_SCALPER', event='event'):
    return SimpleNamespace(engine=SimpleNamespace(value=engine), event_id=event, symbol='XYZ',
        captured_at=NOW,bid=10,ask=10.01,bid_at=NOW,ask_at=NOW)


def context():
    return SimpleNamespace(payload=lambda _:dict(baseline_result='BUY',bars=[]))


def test_durable_budget_caps_engine_calls_and_preserves_desk_allocation(trial):
    budget=TeamBudget(trial,clock=lambda:NOW)
    for engine in ('QUICK_SCALPER','WARRIOR_MOMENTUM_V1'):
        for i in range(20):budget.reserve(engine,'ANALYST',{'event_id':str(i)})
    with pytest.raises(GrokUnavailable,match='CAP'):
        budget.reserve('WARRIOR_MOMENTUM_V1','ANALYST',{'event_id':'extra'})
    restarted=TeamBudget(trial,clock=lambda:NOW)
    for i in range(20):restarted.reserve('NEWS_DESK','NEWS',{'as_of':str(i)})
    assert restarted.report()['reserved_attempts']==60
    assert restarted.report()['unfinalized_attempts']==60
    with pytest.raises(GrokUnavailable,match='CAP'):restarted.reserve('NEWS_DESK','SCOUT',{})


def test_concurrent_process_style_reservations_cannot_overspend(trial):
    def attempt(i):
        try:return TeamBudget(trial,clock=lambda:NOW).reserve('QUICK_SCALPER' if i%2 else 'WARRIOR_MOMENTUM_V1','ANALYST',{'event_id':str(i)})
        except GrokUnavailable:return None
    TeamBudget(trial,clock=lambda:NOW)
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(attempt,range(60)))
    assert sum(r is not None for r in results)==40


def test_pause_expiry_unknown_role_and_duplicate_event_refuse(trial):
    b=TeamBudget(trial,clock=lambda:NOW)
    b.reserve('QUICK_SCALPER','ANALYST',{'event_id':'same'})
    with pytest.raises(GrokUnavailable,match='DUPLICATE'):b.reserve('QUICK_SCALPER','ANALYST',{'event_id':'same'})
    with pytest.raises(ValueError,match='ROLE'):b.reserve('CRYPTO','ANALYST',{})
    (trial/'STOP_GROK').touch()
    with pytest.raises(GrokUnavailable,match='STOPPED'):b.reserve('QUICK_SCALPER','COORDINATOR',{})
    (trial/'STOP_GROK').unlink()
    expired=TeamBudget(trial,clock=lambda:NOW+timedelta(hours=9))
    with pytest.raises(ValueError,match='EXPIRED'):expired.reserve('NEWS_DESK','NEWS',{})


def test_analyst_handoff_stays_with_own_engine_and_never_grants_orders(trial):
    teams,clients=setup(trial)
    result=review_team(teams,snapshot(),context(),clock=lambda:NOW+timedelta(seconds=1))
    assert clients[0].request.call_count==0 and clients[2].request.call_count==0
    calls=clients[1].request.call_args_list
    assert [c.args[0] for c in calls]==['QUICK_SCALPER','MASTER']
    assert calls[1].args[2]['strategy_evidence']['same_engine_analyst']['event_id']=='event'
    assert 'baseline_result' not in calls[0].args[2]['strategy_evidence']
    assert result['within_engine_timing_budget']
    assert result['orders']==result['simulated_fills']==0 and not result['execution_authorized']
    assert teams.budget.report()['reserved_attempts']==2


def test_slow_or_failed_scalper_never_rewrites_time_or_blocks_other_team(trial):
    teams,clients=setup(trial)
    clients[1].request.side_effect=RuntimeError('private provider text')
    failed=review_team(teams,snapshot(),context(),clock=lambda:NOW+timedelta(seconds=20))
    assert failed['status']=='PROVIDER_OR_PROPOSAL_UNAVAILABLE'
    assert 'private provider text' not in json.dumps(failed)
    assert clients[1].request.call_count==1
    other=review_team(teams,snapshot('WARRIOR_MOMENTUM_V1'),context(),clock=lambda:NOW+timedelta(seconds=8))
    assert other['within_engine_timing_budget'] and clients[0].request.call_count==2
    assert not failed['within_engine_timing_budget'] and failed['source_captured_at']==NOW.isoformat()


def test_shared_news_must_be_cited_fresh_and_available_before_native_event(trial):
    teams,_=setup(trial)
    news=dict(status='CITED_DIGEST',execution_authorized=False,digest='Official fixture',
        citations=['https://example.com/announcement'],observed_at=(NOW-timedelta(minutes=1)).isoformat(),
        expires_at=(NOW+timedelta(minutes=14)).isoformat())
    teams._news=dict(news=news,scout={'watchlist':[]},completed_at=(NOW-timedelta(seconds=1)).isoformat())
    assert 'news' in teams.shared_evidence(NOW)
    teams._news['completed_at']=(NOW+timedelta(seconds=1)).isoformat()
    assert teams.shared_evidence(NOW)['status']=='NEWS_NOT_YET_AVAILABLE_AT_EVENT'
    assert teams.shared_evidence(NOW+timedelta(hours=1))['status']=='NO_PRIOR_FRESH_CITED_NEWS'


def test_news_worker_shares_cited_evidence_without_order_authority(trial,monkeypatch):
    import app.asset_modules.grok_engine_teams as module
    teams,clients=setup(trial)
    teams._symbols={'XYZ'}
    monkeypatch.setattr(module,'search_news',lambda *a:dict(status='CITED_DIGEST',digest='Fixture',
        citations=['https://example.com/announcement'],usage={'input_tokens':1}))
    clients[2].request.return_value={'watchlist':[],'reason':'No fixture candidate'}
    class StopAfterCycle:
        def is_set(self):return False
        def wait(self,seconds):return True
    teams._stop=StopAfterCycle()
    teams._news_worker()
    assert teams._news['news']['execution_authorized'] is False
    assert teams._news['scout']['watchlist']==[]
    assert teams.budget.report()['reserved_attempts']==2
    assert clients[0].request.call_count==clients[1].request.call_count==0


def test_day_runner_persists_actual_two_stage_requests_on_same_captured_event(tmp_path,monkeypatch):
    from dataclasses import replace
    from hashlib import sha256
    import time
    import app.asset_modules.grok_day_proposals as day
    import app.asset_modules.grok_engine_teams as module
    import app.configuration.loader as loader
    import app.equity_overhaul.paper_experiment as experiment
    from app.asset_modules.daily_research_journal import canonical
    from app.asset_modules.grok_observer_capture import _encode
    from app.asset_modules.grok import GrokClient
    from tests.asset_modules.test_grok_observer import snap, NOW as AT, BASE
    from tests.asset_modules.test_grok_observer_context import evidence
    repo=tmp_path/'repo';repo.mkdir();root=tmp_path/'trial'
    spec=experiment.create(root,clock=lambda:AT,profile='CENT_EXECUTABLE_V3')
    original_load=experiment.load
    monkeypatch.setattr(experiment,'load',lambda path,*a,**k:original_load(path,clock=lambda:AT))
    session=tmp_path/'daily';session.mkdir();private=session/'private-capture';private.mkdir()
    (root/'recording-session.json').write_text(json.dumps({'session':str(session),'trial_id':spec['trial_id']}))
    source={'app/x.py':'a'*64}
    (session/'research-manifest.json').write_text(json.dumps(dict(source_sha256=sha256(canonical(source)).hexdigest(),
        configuration_sha256='b'*64,baseline_sha256=BASE,paper_experiment=spec)))
    s=replace(snap(),source='WEBULL_OPENAPI');c=replace(evidence(s),source='WEBULL_OPENAPI')
    (private/'private-observer-context.jsonl').write_bytes(_encode(s,c,BASE,AT,'WEBULL_PROVIDER_QUOTE_TIME_SHARED'))
    monkeypatch.setattr(day,'source_manifest',lambda _:source)
    monkeypatch.setattr(day,'configuration_digest',lambda _:'b'*64)
    monkeypatch.setattr(loader,'load_configuration',lambda _:SimpleNamespace(environment=SimpleNamespace(value='PAPER'),
        live_trading_enabled=False,grok_paper_entries_enabled=False))
    requests=[]
    def transport(request):
        requests.append(json.loads(request.data))
        return json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',status='completed',
            content=[dict(type='output_text',text=json.dumps(answer()))])])).encode()
    def factory(*a,**k):return GrokClient('fixture-only','grok-4.7',transport=transport)
    original_teams=module.EngineTeams
    monkeypatch.setattr(module,'EngineTeams',lambda env,path,clock:original_teams(env,path,clock=clock,factory=factory))
    monkeypatch.setattr(original_teams,'offer_symbol',lambda *a:None)
    monkeypatch.setattr(day,'configured_client',lambda *a,**k:factory())
    ticks=[0]
    def pause(seconds):ticks[0]+=seconds;time.sleep(.01)
    day.run(repo,root,max_seconds=3,clock=lambda:AT,timer=lambda:ticks[0],pause=pause,teams=True)
    summaries=list((root/'grok-team-observer').glob('run-*/summary.json'))
    result=json.loads(summaries[0].read_text())
    assert result['records']==1 and result['shared_budget']['reserved_attempts']==2
    assert len(requests)==2 and all('tools' not in r for r in requests)
    assert result['orders']==result['simulated_fills']==0 and result['accounting_final']
    payload=json.loads(requests[1]['input'][1]['content'])
    assert payload['team_engine']=='WARRIOR_MOMENTUM_V1'
    assert payload['strategy_evidence']['same_engine_analyst']['event_id']==s.event_id


def test_restart_pacing_preserves_every_future_hour_without_spending_early(trial):
    from datetime import timedelta
    for hour in range(8):
        at=NOW+timedelta(hours=hour)
        b=TeamBudget(trial,clock=lambda:at)
        for engine in ('QUICK_SCALPER','WARRIOR_MOMENTUM_V1'):
            assert b.claim_sample(engine,'case'+str(hour))
            assert not TeamBudget(trial,clock=lambda:at).claim_sample(engine,'restart')
            for stage in ('ANALYST','COORDINATOR'):
                b.reserve(engine,stage,{'event_id':'case'+str(hour)})
    r=TeamBudget(trial,clock=lambda:NOW+timedelta(hours=7)).report()
    assert r['reserved_attempts']==32 and r['maximum_attempts']==60


def test_news_slots_fit_twenty_calls_through_full_eight_hours(trial):
    slots=[0]+[55+60*i for i in range(8)]
    for minutes in slots:
        b=TeamBudget(trial,clock=lambda:NOW+timedelta(minutes=minutes))
        assert b.claim_sample('NEWS_DESK','desk')
        assert not b.claim_sample('NEWS_DESK','restart')
        for stage in ('NEWS','SCOUT'):b.reserve('NEWS_DESK',stage,{})
    assert b.report()['reserved_attempts']==18

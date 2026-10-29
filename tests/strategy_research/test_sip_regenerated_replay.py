from contextlib import redirect_stdout
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
from hashlib import sha256
import io
import json
from pathlib import Path
import pytest

from app.strategy_research.episode_price_study import PriceBar
from app.strategy_research.sip_regenerated_replay import generate, run, frozen_plan
from tests.strategy_research.test_episode_price_study import file_bars


def fixture_bars():
    start=datetime(2026,6,1,14,tzinfo=UTC)
    values=[('1','1.1','.99','1.05'),('1.05','1.2','1.02','1.15'),('1.15','1.3','1.1','1.25'),
        ('1.25','1.3','1.2','1.22'),('1.22','1.32','1.2','1.3'),('1.3','1.5','1.29','1.45')]
    values += [('1.45','1.7','1.44','1.6')]*8
    return tuple(PriceBar('XYZ',start+timedelta(minutes=i),*[D(v) for v in row],D(10000)) for i,row in enumerate(values))


def capture_fixture(root, empty=False):
    root.mkdir();normalized=root/'normalized';normalized.mkdir()
    values=fixture_bars();p=normalized/'XYZ_2026-06-01.jsonl';file_bars(p,values)
    parts=[dict(symbol='XYZ',day='2026-06-01',rows=len(values),sha256=sha256(p.read_bytes()).hexdigest())]
    if empty:
        p=normalized/'ZZZ_2026-06-01.jsonl';p.write_text('')
        parts.append(dict(symbol='ZZZ',day='2026-06-01',rows=0,sha256=sha256(b'').hexdigest()))
    manifest=dict(scope='SELECTED_HISTORICAL_SIP_CAPTURE_V1',feed='sip',adjustment='raw',status='COMPLETE',
        selected_partitions=len(parts),partitions=parts)
    (root/'manifest.json').write_text(json.dumps(manifest))


def test_real_detectors_generate_signal_without_old_labels():
    events, counts=generate(fixture_bars())
    assert events
    assert len({(e['families'][0],e['anchor']) for e in events})==len(events)
    assert all(e['prefix_ok'] and e['context_sha256'] for e in events)
    assert all(datetime.fromisoformat(e['entry_at']) > datetime.fromisoformat(e['detected_at']) for e in events)


def test_future_bars_cannot_change_prior_signals():
    values=fixture_bars()
    original,_=generate(values[:8])
    changed=values[:8]+tuple(replace(b,open=D(100),high=D(110),low=D(90),close=D(105)) for b in values[8:])
    later,_=generate(changed)
    cutoff=values[7].timestamp+timedelta(minutes=1)
    assert original==[e for e in later if datetime.fromisoformat(e['detected_at'])<=cutoff]


def test_end_to_end_has_entries_and_preserves_capture(tmp_path):
    history=tmp_path/'history';capture_fixture(history)
    original={p:p.read_bytes() for p in history.rglob('*') if p.is_file()}
    with redirect_stdout(io.StringIO()):result=run(history,tmp_path/'out')
    assert result['evidence_status']=='REGENERATED_SIP_PRICE_REPLAY_WITH_ENTRIES'
    assert result['detector_counts']['unique_family_signals']>0
    assert len(result['cases'])==40
    assert result['network_requests']==result['orders']==result['grok_requests']==0
    assert (tmp_path/'out'/'frozen-plan.json').exists()
    assert original=={p:p.read_bytes() for p in original}


def test_empty_partition_excludes_day_from_all_headlines(tmp_path):
    history=tmp_path/'history';capture_fixture(history,empty=True)
    with redirect_stdout(io.StringIO()):result=run(history,tmp_path/'out')
    assert result['empty_partitions']==[dict(symbol='ZZZ',day='2026-06-01')]
    assert all(c['excluded_price_error_days']==1 and c['headline_complete_days']==0 for c in result['cases'])
    assert all(not days for days in result['common_resolved_dates'].values())


def test_tampered_prices_fail_instead_of_replaying(tmp_path):
    history=tmp_path/'history';capture_fixture(history)
    p=history/'normalized'/'XYZ_2026-06-01.jsonl';file_bars(p,fixture_bars()[:3])
    with pytest.raises(ValueError,match='HASH_OR_ROW_MISMATCH'):run(history,tmp_path/'out')
    assert not (tmp_path/'out'/'sip-regenerated-replay-summary.json').exists()


def test_plan_preserves_retrospective_and_nonexecuting_scope():
    plan=frozen_plan()
    assert 'not a fresh holdout' in plan['selection']
    assert plan['costs']==['0.01','0.02']
    assert len(plan['families'])==5 and len(plan['exits'])==4

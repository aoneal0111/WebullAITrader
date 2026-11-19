from datetime import UTC, datetime, timedelta
import json
import sqlite3
import pytest
from app.strategy_research.warrior_native_timing_review import native_gates, native_calls, readonly, collect

NOW=datetime(2026,11,19,14,tzinfo=UTC)
START=NOW-timedelta(hours=1)


def database(path):
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE capture_records(sequence INTEGER PRIMARY KEY, record_type TEXT, symbol TEXT, timestamp TEXT, payload_json TEXT)')
    return db


def pair(db,seq,signal,evaluation,*,life='WARRIOR_MOMENTUM_V1|ABC|one',symbol='ABC'):
    data=dict(lifecycle_id=life,to='AUTHORIZATION_EVALUATED',authorization_reason='AI_REVIEW_NOT_SENT',
              decision_timestamp=evaluation.isoformat(),provider_bid_timestamp=evaluation.isoformat())
    db.execute('INSERT INTO capture_records VALUES(?,?,?,?,?)',(seq,'STATE_TRANSITION',symbol,evaluation.isoformat(),json.dumps(data)))
    db.execute('INSERT INTO capture_records VALUES(?,?,?,?,?)',(seq+1,'EXECUTION_GATE_DECISION',symbol,signal.isoformat(),
        json.dumps(dict(lifecycle=life,final_reason='AI_REVIEW_NOT_SENT'))))
    db.commit()


@pytest.mark.parametrize('offset,classification',[
    (1,'SIGNAL_FUTURE_AT_EVALUATION'),(-60,'SIGNAL_EXPIRED_BY_EVALUATION'),
    (-30,'PRODUCER_CLOCK_WINDOW_REQUIRES_ACTUAL_CHECK_TIME'),(0,'PRODUCER_CLOCK_WINDOW_REQUIRES_ACTUAL_CHECK_TIME')])
def test_pair_classifies_only_what_recorded_times_prove(tmp_path,offset,classification):
    path=tmp_path/'native.sqlite3';db=database(path)
    pair(db,1,NOW+timedelta(seconds=offset),NOW);db.close()
    # Future signal requires capture end to include its recorded time.
    report=native_gates(path,START,NOW+timedelta(seconds=2))
    assert report['rows'][0]['classification']==classification
    assert report['rows'][0]['signal_age_at_evaluation_seconds']==-offset
    assert report['rows'][0]['producer_check_time_recorded'] is False


def test_wal_snapshot_includes_committed_rows_and_never_changes_source(tmp_path):
    path=tmp_path/'native.sqlite3';db=database(path);db.execute('PRAGMA journal_mode=WAL')
    pair(db,1,NOW,NOW)
    before=db.total_changes
    assert native_gates(path,START,NOW)['rows'][0]['sequence']==2
    with readonly(path) as read:
        with pytest.raises(sqlite3.OperationalError):read.execute('DELETE FROM capture_records')
    assert db.total_changes==before and db.execute('SELECT count(*) FROM capture_records').fetchone()[0]==2
    db.close()


def test_wrong_lifecycle_or_prior_session_is_not_matched(tmp_path):
    path=tmp_path/'native.sqlite3';db=database(path);pair(db,1,NOW,NOW)
    db.execute("UPDATE capture_records SET payload_json=? WHERE sequence=1",(json.dumps(dict(
        lifecycle_id='another',to='AUTHORIZATION_EVALUATED',authorization_reason='AI_REVIEW_NOT_SENT',decision_timestamp=NOW.isoformat())),))
    db.commit();db.close()
    assert native_gates(path,START,NOW)['rows'][0]['classification']=='EVALUATION_PAIR_UNAVAILABLE'
    assert native_gates(path,NOW+timedelta(seconds=61),NOW+timedelta(seconds=62))['rows']==[]


def test_missing_database_is_not_created(tmp_path):
    path=tmp_path/'absent.sqlite3'
    with pytest.raises(FileNotFoundError):native_gates(path,START,NOW)
    assert not path.exists()


def test_native_model_reason_is_session_scoped_and_context_is_not_exported(tmp_path):
    path=tmp_path/'budget.sqlite3';db=sqlite3.connect(path)
    db.execute('CREATE TABLE calls(engine TEXT,stage TEXT,started TEXT,result TEXT,context TEXT)')
    for stage,at in [('NATIVE_REVIEW',NOW),('NATIVE_REVIEW',START-timedelta(days=1)),('DIRECTOR',NOW)]:
        db.execute('INSERT INTO calls VALUES(?,?,?,?,?)',('WARRIOR_MOMENTUM_V1',stage,at.isoformat(),
            json.dumps(dict(status='RESPONSE_RECEIVED',latency_ms=7000,answer=dict(selected_ids=[],reason='No justified entry'),
                usage_cumulative_not_per_request={'PRIVATE':'not exported'})),'PRIVATE_CONTEXT'))
    db.commit();db.close()
    rows=native_calls(path,START,NOW)
    assert len(rows)==1 and rows[0]['reason']=='No justified entry'
    assert 'PRIVATE' not in json.dumps(rows)


def operation(root):
    paper=root/'paper';paper.mkdir();session=root/'session';session.mkdir()
    spec={'trial_id':'one'}
    (paper/'experiment.json').write_text(json.dumps(spec))
    (paper/'recording-session.json').write_text(json.dumps(dict(trial_id='one',session=str(session))))
    (session/'research-manifest.json').write_text(json.dumps(dict(paper_experiment=spec,started_at=START.isoformat(),live_trading_enabled=False)))
    return paper,session


def test_collect_keeps_read_errors_distinct_and_rejects_unrelated_runtime(tmp_path):
    paper,_=operation(tmp_path);diagnostics=tmp_path/'diagnostics';diagnostics.mkdir()
    (diagnostics/'fixture.json').write_text(json.dumps(dict(run_id='fixture',captured_at=NOW.isoformat(),entry_conversion={'counters':{}},metrics={'market_events_received':1})))
    result=collect(paper,tmp_path/'missing.sqlite3',diagnostics,now=NOW)
    assert result['native_gate_evidence']['read_error']=='FileNotFoundError'
    assert result['runtime_candidates']==[] and result['orders']==result['provider_requests']==result['grok_requests']==0


def test_pointer_or_manifest_mismatch_is_refused(tmp_path):
    paper,_=operation(tmp_path)
    (paper/'experiment.json').write_text(json.dumps({'trial_id':'other'}))
    with pytest.raises(ValueError,match='EXACT_PAPER_RECORDING'):collect(paper,tmp_path/'unused',tmp_path,now=NOW)

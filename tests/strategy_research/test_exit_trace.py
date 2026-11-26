from datetime import UTC, datetime, timedelta
import json
import pytest
from app.asset_modules.daily_research_journal import DailyJournal
from app.strategy_research.exit_trace import extract

NOW=datetime(2026,10,29,19,51,tzinfo=UTC)


def fixture(tmp_path):
    session=tmp_path/'session';session.mkdir()
    journal=DailyJournal(session/'journal','a'*64,clock=lambda:NOW)
    journal.submit('PAPER_EVENT',dict(symbol='PRCH',timestamp=NOW.isoformat(),event_type='ORDER_FILLED'))
    journal.submit('PAPER_EVENT',dict(symbol='PRCH',timestamp=(NOW-timedelta(days=1)).isoformat(),event_type='ORDER_FILLED'))
    for _ in range(3):journal.submit('MARKET_EVENT',dict(symbol='PRCH',price='20'))
    journal.submit('MARKET_EVENT',dict(symbol='OTHER',price='20'))
    receipt=journal.close();assert receipt['writer_stopped']
    (session/'research-summary.json').write_text(json.dumps(dict(baseline_sha256='a'*64,journal=receipt)))
    (session/'research-manifest.json').write_text(json.dumps(dict(baseline_sha256='a'*64,started_at=(NOW-timedelta(seconds=10)).isoformat())))
    audit=tmp_path/'audit.json';audit.write_text(json.dumps(dict(source=dict(outcomes=[dict(
        instrument='PRCH',opened_at=NOW.isoformat(),closed_at=(NOW+timedelta(seconds=1)).isoformat())]))))
    return session,audit


def test_verified_selection_retains_orders_and_explicitly_samples_context(tmp_path):
    session,audit=fixture(tmp_path)
    # Move the final summary end past the synthetic close (the chain is untouched).
    p=session/'research-summary.json';s=json.loads(p.read_text());s['journal']['updated_at']=(NOW+timedelta(seconds=3)).isoformat();p.write_text(json.dumps(s))
    before={p:p.read_bytes() for p in session.rglob('*') if p.is_file()}
    result=extract(session,audit,tmp_path/'output')
    assert result['source']['records']==6 and result['counts']=={'PAPER_EVENT':1,'MARKET_EVENT':1}
    assert result['coalesced']=={'MARKET_EVENT':2} and not result['output_capacity_reached']
    assert result['orders']==result['grok_requests']==result['network_requests']==0
    assert all(p.read_bytes()==data for p,data in before.items())


def test_tampered_source_refused_before_any_export(tmp_path):
    session,audit=fixture(tmp_path);p=next((session/'journal').glob('events-*'))
    p.write_bytes(p.read_bytes().replace(b'20',b'21',1))
    with pytest.raises(ValueError):extract(session,audit,tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_unclosed_source_refused(tmp_path):
    session,audit=fixture(tmp_path);p=session/'journal/journal-status.json'
    s=json.loads(p.read_text());s['closed']=False;p.write_text(json.dumps(s))
    with pytest.raises(ValueError):extract(session,audit,tmp_path/'output')


def test_mismatched_baseline_refused(tmp_path):
    session,audit=fixture(tmp_path);p=session/'research-manifest.json'
    s=json.loads(p.read_text());s['baseline_sha256']='b'*64;p.write_text(json.dumps(s))
    with pytest.raises(ValueError,match='MISMATCH'):extract(session,audit,tmp_path/'output')


def test_capacity_is_reported_not_disguised_as_complete(tmp_path,monkeypatch):
    session,audit=fixture(tmp_path);p=session/'research-summary.json'
    s=json.loads(p.read_text());s['journal']['updated_at']=(NOW+timedelta(seconds=3)).isoformat();p.write_text(json.dumps(s))
    monkeypatch.setattr('app.strategy_research.exit_trace.MAX_OUTPUT_BYTES',1)
    result=extract(session,audit,tmp_path/'output')
    assert result['output_capacity_reached'] and result['written_records']==0

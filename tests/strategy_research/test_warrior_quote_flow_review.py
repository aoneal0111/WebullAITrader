from datetime import UTC,datetime,timedelta
from hashlib import sha256
import json
from zipfile import ZipFile
import pytest
from app.strategy_research.warrior_quote_flow_review import export,canonical,Flow

NOW=datetime(2026,11,17,18,tzinfo=UTC)
BASE='a'*64


def fixture(tmp_path,*,alter_line=False,closed=True):
    session=tmp_path/'session';(session/'journal').mkdir(parents=True)
    old=(NOW-timedelta(seconds=30)).isoformat();now=NOW.isoformat()
    events=[('MARKET_EVENT',dict(source='webull',symbol='XYZ',event_type='TRADE',timestamp=old,
        payload=dict(price='5',trade_id='tick'))),
        ('MARKET_EVENT',dict(source='webull',symbol='XYZ',event_type='QUOTE',timestamp=now,
            payload=dict(bid='4.99',ask='5.01'))),
        ('MARKET_EVENT',dict(source='webull',symbol='XYZ',event_type='TRADE',timestamp=now,
            payload=dict(price='5',trade_id='snapshot-retained-price'))),
        ('ASSESSMENT',dict(engine='WARRIOR_MOMENTUM_V1',assessed_at=now,
            quote_provenance='WEBULL_OPENAPI',quote=dict(symbol='XYZ',
                last_timestamp=old,bid_timestamp=now,ask_timestamp=now),baseline_result='WAIT',setup_state='TRIGGERED')),
        ('ASSESSMENT',dict(engine='QUICK_SCALPER',assessed_at=now,quote={}))]
    previous='0'*64;raw=b''
    for n,(kind,payload) in enumerate(events,1):
        unsigned=dict(sequence=n,previous_sha256=previous,baseline_sha256=BASE,kind=kind,
            received_at=now,payload=payload)
        previous=sha256(canonical(unsigned)).hexdigest()
        raw+=canonical(dict(unsigned,sha256=previous))+b'\n'
    summary=canonical(dict(journal=dict(updated_at=now,closed=closed,writer_alive=not closed,writer_failed=False,
        baseline_sha256=BASE,counts=dict(written=len(events)),bytes_written=len(raw),chain_head=previous)))
    manifest=canonical(dict(baseline_sha256=BASE,started_at=now))
    (session/'journal'/'events-00001.jsonl').write_bytes(raw.replace(b'4.99',b'4.98') if alter_line else raw)
    (session/'research-manifest.json').write_bytes(manifest)
    (session/'research-summary.json').write_bytes(summary)
    review=tmp_path/'review.zip'
    with ZipFile(review,'x') as z:
        z.writestr('paper-day-review.json',canonical(dict(scope='PRIVATE_CLOSED_PAPER_DAY_REVIEW_V1',recording_session=str(session))))
        z.writestr('capture/research-manifest.json',manifest)
        z.writestr('capture/research-summary.json',summary)
    return review,session


def test_verified_flow_separates_borrowed_price_from_actual_trade_and_has_no_scalper_results(tmp_path):
    review,session=fixture(tmp_path)
    before=(session/'journal'/'events-00001.jsonl').read_bytes()
    output=tmp_path/'output.zip';report=export(review,output)
    counts=report['source_counts']['WEBULL_OPENAPI']
    assert counts['ASSESSMENTS']==1 and counts['LAST_OLDER_THAN_5S']==1
    assert counts['RECORDED_TRADE_OLDER_THAN_5S']==1
    assert counts['RECORDED_BOOK_WITHIN_2S']==1
    assert report['market_event_counts']['BORROWED_PRICE_NOT_TRADE_EVIDENCE']==1
    assert report['verified_records']==5 and report['orders']==report['provider_requests']==0
    assert (session/'journal'/'events-00001.jsonl').read_bytes()==before
    with ZipFile(output) as z:assert z.namelist()==['warrior-quote-flow.json']


@pytest.mark.parametrize('change',['line','metadata','open','coverage'])
def test_changed_or_unfinalized_sources_are_refused(tmp_path,change):
    review,session=fixture(tmp_path,alter_line=change=='line',closed=change!='open')
    if change=='metadata':(session/'research-manifest.json').write_text('{}')
    if change=='coverage':(session/'journal'/'events-00001.jsonl').write_bytes(b'')
    output=tmp_path/'output.zip'
    with pytest.raises(ValueError):export(review,output)
    assert not output.exists()


def test_exact_runtime_requires_hash_and_run_identity(tmp_path):
    review,_=fixture(tmp_path);p=tmp_path/'runtime.json';raw=canonical(dict(run_id='b'*32,captured_at=NOW.isoformat(),process_started_at=NOW.isoformat()));p.write_bytes(raw)
    for identity,digest in [('c'*32,sha256(raw).hexdigest()),('b'*32,'0'*64)]:
        with pytest.raises(ValueError,match='EXACT_DIAGNOSTIC_CHANGED'):
            export(review,tmp_path/'bad.zip',diagnostic=p,run_id=identity,diagnostic_sha256=digest)
    export(review,tmp_path/'good.zip',diagnostic=p,run_id='b'*32,diagnostic_sha256=sha256(raw).hexdigest())
    with ZipFile(tmp_path/'good.zip') as z:assert z.read('exact-runtime.json')==raw


def test_fresh_recorded_trade_and_book_are_distinguished_from_old_offered_rest():
    flow=Flow();now=NOW.isoformat()
    for kind,payload in [('TRADE',dict(trade_id='tick',price='5')),('QUOTE',dict(bid='4.99',ask='5.01'))]:
        flow.consume(dict(kind='MARKET_EVENT',payload=dict(source='webull',symbol='XYZ',event_type=kind,timestamp=now,payload=payload)))
    flow.consume(dict(kind='ASSESSMENT',payload=dict(engine='WARRIOR_MOMENTUM_V1',assessed_at=now,
        quote_provenance='WEBULL_OPENAPI',quote=dict(symbol='XYZ',bid_timestamp=(NOW-timedelta(seconds=6)).isoformat(),
            ask_timestamp=now,last_timestamp=now))))
    counts=flow.result()['source_counts']['WEBULL_OPENAPI']
    assert counts['BID_OLDER_THAN_5S']==1 and counts['RECORDED_STREAM_BOTH_WITHIN_2S']==1


def test_reconstruction_memory_stays_bounded():
    flow=Flow();now=NOW.isoformat()
    for index in range(1000):
        symbol='S'+str(index)
        flow.consume(dict(kind='MARKET_EVENT',payload=dict(source='webull',symbol=symbol,event_type='TRADE',timestamp=now,payload=dict(trade_id='tick',price='5'))))
        flow.consume(dict(kind='ASSESSMENT',payload=dict(engine='WARRIOR_MOMENTUM_V1',assessed_at=now,quote_provenance='WEBULL_OPENAPI',quote=dict(symbol=symbol))))
    assert len(flow.states)==len(flow.per_symbol)==512
    assert flow.result()['source_counts']['WEBULL_OPENAPI']['ASSESSMENTS']==1000
    assert all(len(samples)<=3 for samples in flow.samples.values())

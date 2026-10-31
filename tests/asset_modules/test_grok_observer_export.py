from datetime import UTC,datetime,timedelta
from contextlib import closing
from hashlib import sha256
import json
import sqlite3
from unittest.mock import patch

import pytest

from app.asset_modules.grok_observer_export import archive,SEQUENCE_WINDOW
from app.asset_modules.grok_observer_input import load_snapshots

NOW=datetime(2026,10,24,14,tzinfo=UTC)


def source(path,*,bad=False):
    db=sqlite3.connect(path)
    try:
        db.executescript('CREATE TABLE capture_metadata(singleton INTEGER,schema_version INTEGER);'
            'INSERT INTO capture_metadata VALUES(1,1);'
            'CREATE TABLE capture_records(sequence INTEGER PRIMARY KEY,record_type TEXT,symbol TEXT,timestamp TEXT,payload_json TEXT);')
        bar=dict(completed=True,interval='1m',bar_timestamp=(NOW-timedelta(minutes=1)).isoformat(),
            completion_timestamp=NOW.isoformat(),open='100',high='101',low='99',close='100',volume='1000')
        quote=dict(action='QUOTE',quote_timestamp=NOW.isoformat(),bid='100',ask='100.1',
            lifecycle_id='QUICK_SCALPER|private-identity',account_id='PRIVATE_ACCOUNT',pnl='PRIVATE_PNL',
            reason='PRIVATE_TEXT',provider_bid_timestamp=NOW.isoformat())
        db.executemany('INSERT INTO capture_records VALUES(?,?,?,?,?)',[
            (1,'MINUTE_BAR','AAPL',NOW.isoformat(),json.dumps(bar)),
            (2,'EXECUTION_PRICE_PATH','AAPL',NOW.isoformat(),json.dumps(quote)),
            (3,'EXECUTION_PRICE_PATH','AAPL',NOW.isoformat(),json.dumps(dict(action='FILL',quantity='PRIVATE_QTY')))])
        if bad:db.execute('INSERT INTO capture_records VALUES(4,?,?,?,?)',('MINUTE_BAR','AAPL',NOW.isoformat(),'PRIVATE_BAD_JSON'))
        db.commit()
    finally:db.close()


def test_export_is_read_only_minimal_and_never_an_online_snapshot(tmp_path):
    db=tmp_path/'capture.sqlite3';source(db)
    before=sha256(db.read_bytes()).hexdigest()
    output=tmp_path/'output'
    with (patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('network forbidden')),
          patch('os.getenv',side_effect=AssertionError('credentials forbidden'))):
        report=archive(tmp_path,db,output)
    assert sha256(db.read_bytes()).hexdigest()==before
    assert report['status']=='ARCHIVE_ONLY_NOT_ONLINE_READY' and not report['online_observer_ready']
    assert report['bars_exported']==report['quotes_exported']==1
    data=(output/'private-market-evidence.jsonl').read_text()
    assert 'PRIVATE_' not in data and 'PRIVATE_' not in json.dumps(report)
    rows=[json.loads(line) for line in data.splitlines()]
    assert rows[1]['owner_from_recorded_lifecycle']=='QUICK_SCALPER'
    assert rows[1]['independent_side_timestamps']=='UNAVAILABLE'
    assert 'bid_at' not in rows[1] and 'ask_at' not in rows[1]
    with pytest.raises(ValueError):load_snapshots(output/'private-market-evidence.jsonl','a'*64)


def test_missing_source_is_not_created_and_status_is_explicit(tmp_path):
    missing=tmp_path/'missing.sqlite3'
    report=archive(tmp_path,missing,tmp_path/'output')
    assert report['status']=='SOURCE_MISSING' and not missing.exists()
    assert not report['online_observer_ready'] and not report['source_opened_read_only']


def test_bad_rows_are_counted_without_copying_payloads(tmp_path):
    db=tmp_path/'capture.sqlite3';source(db,bad=True)
    report=archive(tmp_path,db,tmp_path/'output')
    assert report['rejected_rows']==1 and report['bars_exported']==1
    assert report['rejection_counts']=={'INVALID_JSON':1} and report['skipped_non_quote_rows']==1
    assert report['rows_read']==report['bars_exported']+report['quotes_exported']+report['rejected_rows']+report['skipped_non_quote_rows']+report['lookahead_rows']
    assert 'PRIVATE_' not in (tmp_path/'output'/'summary.json').read_text()


def test_unknown_schema_is_sanitized_and_never_creates_archive_data(tmp_path):
    db=tmp_path/'capture.sqlite3';source(db)
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute('UPDATE capture_metadata SET schema_version=99')
    report=archive(tmp_path,db,tmp_path/'output')
    assert report['status']=='SOURCE_READ_FAILED' and report['error_type']=='ValueError'
    assert report['files']==[] and not (tmp_path/'output'/'private-market-evidence.jsonl').exists()


def test_sequence_scan_bound_is_visible_and_never_treated_as_full_coverage(tmp_path):
    db=tmp_path/'capture.sqlite3';source(db)
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute('INSERT INTO capture_records VALUES(?,?,?,?,?)',
            (SEQUENCE_WINDOW+10,'DECISION','AAPL',NOW.isoformat(),'{}'))
    report=archive(tmp_path,db,tmp_path/'output')
    assert report['scan_limited'] and report['rows_read']==0 and not report['online_observer_ready']


def test_existing_output_is_not_overwritten(tmp_path):
    db=tmp_path/'capture.sqlite3';source(db)
    output=tmp_path/'output';archive(tmp_path,db,output)
    with pytest.raises(FileExistsError):archive(tmp_path,db,output)


@pytest.mark.parametrize('crossed,future,reason',[(True,False,'CROSSED_QUOTE'),(False,True,'FUTURE_QUOTE'),(True,True,'CROSSED_AND_FUTURE_QUOTE')])
def test_quote_anomaly_causes_and_offsets_are_separate(tmp_path,crossed,future,reason):
    db=tmp_path/'capture.sqlite3';source(db)
    with closing(sqlite3.connect(db)) as connection,connection:
        p=json.loads(connection.execute('SELECT payload_json FROM capture_records WHERE sequence=2').fetchone()[0])
        p['bid']='101' if crossed else '100'
        p['quote_timestamp']=(NOW+timedelta(seconds=1.3) if future else NOW).isoformat()
        connection.execute('UPDATE capture_records SET payload_json=? WHERE sequence=2',(json.dumps(p),))
    report=archive(tmp_path,db,tmp_path/'output')
    assert report['rejection_counts']=={reason:1}
    assert report['quote_anomalies']==dict(crossed=int(crossed),future=int(future),both=int(crossed and future))
    assert report['future_offset_seconds_max']==(1.3 if future else None)

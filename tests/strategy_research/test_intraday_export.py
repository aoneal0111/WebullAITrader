"""Read-only source, bounds and export privacy checks; stdlib only."""
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from app.strategy_research.intraday_export import export_sources

AT = datetime(2026, 10, 22, 16, tzinfo=UTC)


def equity(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE capture_metadata(singleton INTEGER, schema_version INTEGER)')
        db.execute('INSERT INTO capture_metadata VALUES(1,1)')
        db.execute('CREATE TABLE capture_records(sequence INTEGER, record_type TEXT, symbol TEXT, timestamp TEXT, payload_json TEXT)')
        bar = dict(bar_timestamp=(AT-timedelta(minutes=1)).isoformat(), completion_timestamp=AT.isoformat(),
                   interval='1m', completed=True, open='100', high='101', low='99', close='100.5', volume='1000',
                   private_extra='PRIVATE_VALUE')
        quote = dict(action='QUOTE', quote_timestamp=AT.isoformat(), bid='100', ask='100.5',
                     lifecycle_id='PRIVATE_LIFECYCLE', paper_campaign_id='PRIVATE_CAMPAIGN', stale_quote=True)
        for i,(kind,p) in enumerate([('MINUTE_BAR',bar),('EXECUTION_PRICE_PATH',quote),('PAPER_FILL',{'secret':'PRIVATE_VALUE'})]):
            db.execute('INSERT INTO capture_records VALUES(?,?,?,?,?)',(i,kind,'TEST',AT.isoformat(),json.dumps(p)))


class ExportTests(unittest.TestCase):
    def test_sources_remain_unchanged_and_only_allowlisted_market_fields_leave(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);db=root/'source.sqlite3';equity(db);before=sha256(db.read_bytes()).hexdigest()
            result,archive=export_sources(root/'out',equity=db,crypto=root/'missing',at=AT)
            self.assertEqual(result['sources'][0]['exported'],2)
            self.assertEqual(result['sources'][1]['status'],'MISSING')
            self.assertFalse(result['execution_authorized'])
            self.assertFalse((root/'missing').exists())
            self.assertEqual(sha256(db.read_bytes()).hexdigest(),before)
            with ZipFile(archive) as z:
                data=z.read('equity-saved-market-data.jsonl')
                self.assertNotIn(b'PRIVATE_',data)
                records=[json.loads(r) for r in data.splitlines()]
                self.assertTrue(next(r for r in records if r['kind']=='QUOTE')['stale_quote'])
                self.assertEqual(z.namelist(),['manifest.json','equity-saved-market-data.jsonl'])

    def test_future_bar_is_counted_as_rejected_and_export_closes_windows_handle(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);db=root/'source';equity(db)
            with closing(sqlite3.connect(db)) as c,c:
                row=json.loads(c.execute("SELECT payload_json FROM capture_records WHERE record_type='MINUTE_BAR'").fetchone()[0])
                row['completion_timestamp']=(AT+timedelta(minutes=1)).isoformat()
                c.execute("UPDATE capture_records SET payload_json=? WHERE record_type='MINUTE_BAR'",(json.dumps(row),))
            result,_=export_sources(root/'out',equity=db,crypto=root/'missing',at=AT)
            self.assertEqual(result['sources'][0]['rejected'],1)
            db.unlink()
            self.assertFalse(db.exists())

    def test_truncation_is_explicit_and_output_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as root,patch('app.strategy_research.intraday_export.LIMIT',1):
            root=Path(root);db=root/'source';equity(db)
            result,archive=export_sources(root/'out',equity=db,crypto=root/'missing',at=AT)
            self.assertTrue(result['sources'][0]['truncated'])
            before=archive.read_bytes()
            with self.assertRaises(FileExistsError):
                export_sources(root/'out',equity=db,crypto=root/'missing',at=AT)
            self.assertEqual(archive.read_bytes(),before)

    def test_crypto_retains_missing_volume_and_recorded_durable_availability(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);db=root/'crypto'
            p=dict(symbol='BTC/USD', interval='M1', opened_at=(AT-timedelta(minutes=1)).isoformat(),
                   closed_at=AT.isoformat(), open='100', high='101', low='99', close='100', volume=None)
            with closing(sqlite3.connect(db)) as c,c:
                c.execute('PRAGMA application_id=0x41544342')
                c.execute('CREATE TABLE capture_metadata(key TEXT,value TEXT)')
                c.execute("INSERT INTO capture_metadata VALUES('schema','CRYPTO_COMPLETED_BAR_CAPTURE_V1')")
                c.execute('CREATE TABLE completed_bars(identity TEXT,interval TEXT,symbol TEXT,available_at TEXT,payload TEXT)')
                c.execute('INSERT INTO completed_bars VALUES(?,?,?,?,?)',('one','M1','BTC/USD',AT.isoformat(),json.dumps(p)))
            result,archive=export_sources(root/'out',equity=root/'missing',crypto=db,at=AT)
            self.assertEqual(result['sources'][1]['volume_missing'],1)
            with ZipFile(archive) as z:
                row=json.loads(z.read('crypto-saved-market-data.jsonl'))
                self.assertIsNone(row['volume'])
                self.assertEqual(row['availability_basis'],'CRYPTO_RECORDED_DURABLE_AVAILABILITY')


if __name__=='__main__':unittest.main()

"""Read-only source, bounds and export privacy checks; stdlib only."""
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import contextlib
import io
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from app.strategy_research.intraday_export import export_sources, EQUITY_PAGE_QUERY, EQUITY_INDEX_QUERY

AT = datetime(2026, 10, 22, 16, tzinfo=UTC)


def equity(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE capture_metadata(singleton INTEGER, schema_version INTEGER)')
        db.execute('INSERT INTO capture_metadata VALUES(1,1)')
        db.execute('CREATE TABLE capture_records(sequence INTEGER PRIMARY KEY, record_type TEXT, symbol TEXT, timestamp TEXT, payload_json TEXT)')
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

    def test_equity_query_uses_sequence_range_without_sorting_entire_type_history(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'source';equity(path)
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('CREATE INDEX capture_type_time ON capture_records(record_type,timestamp,sequence)')
                rows=db.execute('EXPLAIN QUERY PLAN '+EQUITY_PAGE_QUERY,
                    (-1,2,'MINUTE_BAR','EXECUTION_PRICE_PATH',(AT-timedelta(days=1)).isoformat(),AT.isoformat(),100)).fetchall()
                plan=' '.join(str(r[-1]) for r in rows)
                self.assertIn('INTEGER PRIMARY KEY',plan)
                self.assertNotIn('TEMP B-TREE',plan)

    def test_sparse_sequence_scan_stops_at_explicit_budget(self):
        with tempfile.TemporaryDirectory() as root, patch('app.strategy_research.intraday_export.MAX_SEQUENCE_SLOTS',4), patch('app.strategy_research.intraday_export.PAGE_SLOTS',2):
            root=Path(root);db=root/'source';equity(db)
            with closing(sqlite3.connect(db)) as c,c:
                c.execute('INSERT INTO capture_records VALUES(?,?,?,?,?)',(1000000,'PAPER_FILL','TEST',AT.isoformat(),'{}'))
            result,_=export_sources(root/'out',equity=db,crypto=root/'missing',at=AT)
            source=result['sources'][0]
            self.assertEqual(source['status'],'EXPORTED')
            self.assertEqual(source['sequence_slots_scanned'],4)
            self.assertTrue(source['scan_limited'])
            self.assertTrue(source['truncated'])
            self.assertEqual(source['exported'],0)

    def test_source_error_records_safe_sqlite_code_stage_and_failing_status(self):
        def failing_read(db,*args):
            return db.execute('SELECT * FROM PRIVATE_SQLITE_MESSAGE').fetchall()
        with tempfile.TemporaryDirectory() as root, patch('app.strategy_research.intraday_export._equity_rows',side_effect=failing_read):
            root=Path(root);db=root/'source';equity(db)
            result,archive=export_sources(root/'out',equity=db,crypto=root/'missing',at=AT)
            source=result['sources'][0]
            self.assertEqual(source['error_type'],'OperationalError')
            self.assertEqual(source['sqlite_errorcode'],sqlite3.SQLITE_ERROR)
            self.assertEqual(source['sqlite_errorname'],'SQLITE_ERROR')
            self.assertEqual(source['error_stage'],'EQUITY_RECORD_READ')
            self.assertEqual(result['status'],'SOURCE_EXPORT_FAILED')
            with ZipFile(archive) as z:
                self.assertNotIn(b'PRIVATE_SQLITE_MESSAGE',z.read('manifest.json'))

    def test_legacy_non_primary_sequence_schema_is_reported_not_scanned(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);db=root/'source'
            with closing(sqlite3.connect(db)) as c,c:
                c.execute('CREATE TABLE capture_metadata(singleton INTEGER,schema_version INTEGER)')
                c.execute('INSERT INTO capture_metadata VALUES(1,1)')
                c.execute('CREATE TABLE capture_records(sequence INTEGER)')
            result,_=export_sources(root/'out',equity=db,crypto=root/'missing',at=AT)
            self.assertEqual(result['sources'][0]['validation_reason'],'UNKNOWN_EQUITY_SEQUENCE_SCHEMA')

    def test_partial_source_failure_returns_nonzero_and_retains_shareable_diagnostic_zip(self):
        from app.strategy_research.intraday_export import main
        class Clock:
            fromisoformat=staticmethod(datetime.fromisoformat)
            now=staticmethod(lambda zone: AT)
        def failing_read(db,*args):
            return db.execute('SELECT * FROM PRIVATE_SQLITE_MESSAGE').fetchall()
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);eq=root/'equity';cr=root/'crypto';equity(eq)
            payload=dict(symbol='BTC/USD',interval='M1',opened_at=(AT-timedelta(minutes=1)).isoformat(),
                         closed_at=AT.isoformat(),open='100',high='101',low='99',close='100',volume=None)
            with closing(sqlite3.connect(cr)) as db,db:
                db.execute('PRAGMA application_id=0x41544342')
                db.execute('CREATE TABLE capture_metadata(key TEXT,value TEXT)')
                db.execute("INSERT INTO capture_metadata VALUES('schema','CRYPTO_COMPLETED_BAR_CAPTURE_V1')")
                db.execute('CREATE TABLE completed_bars(identity TEXT,interval TEXT,symbol TEXT,available_at TEXT,payload TEXT)')
                db.execute('INSERT INTO completed_bars VALUES(?,?,?,?,?)',('one','M1','BTC/USD',AT.isoformat(),json.dumps(payload)))
            output=io.StringIO()
            with patch('app.strategy_research.intraday_export.datetime',Clock), patch('app.strategy_research.intraday_export._equity_rows',side_effect=failing_read), contextlib.redirect_stdout(output):
                code=main(['--output',str(root/'out'),'--equity',str(eq),'--crypto',str(cr)])
            self.assertEqual(code,2)
            self.assertIn('INTRADAY_ZIP=',output.getvalue())
            with ZipFile(root/'out.zip') as z:
                result=json.loads(z.read('manifest.json'))
                self.assertEqual(result['status'],'PARTIAL_SOURCE_EXPORT')
                self.assertIn('crypto-saved-market-data.jsonl',z.namelist())
                self.assertNotIn(b'PRIVATE_SQLITE_MESSAGE',z.read('manifest.json'))

    def test_query_interrupt_keeps_timeout_failure_visible_and_safe(self):
        from app.read_only_sqlite import read_database as original_read
        @contextlib.contextmanager
        def interrupt_page(path):
            with original_read(path) as db:
                class Connection:
                    def execute(self,query,args=()):
                        if query==EQUITY_PAGE_QUERY:
                            db.set_progress_handler(lambda:1,1)
                        return db.execute(query,args)
                yield Connection()
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);db=root/'source';equity(db)
            with patch('app.strategy_research.intraday_export.read_database',side_effect=interrupt_page):
                result,_=export_sources(root/'out',equity=db,crypto=root/'missing',at=AT)
            error=result['sources'][0]
            self.assertEqual(error['sqlite_errorcode'],sqlite3.SQLITE_INTERRUPT)
            self.assertEqual(error['sqlite_errorname'],'SQLITE_INTERRUPT')
            self.assertEqual(error['error_stage'],'EQUITY_RECORD_READ')
            self.assertEqual(result['status'],'SOURCE_EXPORT_FAILED')


    def test_timeout_after_completed_page_keeps_validated_rows_and_failure(self):
        from app.read_only_sqlite import read_database as original_read
        @contextlib.contextmanager
        def interrupt_second_page(path):
            with original_read(path) as db:
                class Connection:
                    pages=0
                    def execute(self,query,args=()):
                        if query==EQUITY_PAGE_QUERY:
                            self.pages+=1
                            if self.pages==2:
                                db.set_progress_handler(lambda:1,1)
                        return db.execute(query,args)
                yield Connection()
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);db=root/'source';equity(db)
            before=sha256(db.read_bytes()).hexdigest()
            with patch('app.strategy_research.intraday_export.read_database',side_effect=interrupt_second_page), patch('app.strategy_research.intraday_export.PAGE_SLOTS',2):
                result,archive=export_sources(root/'out',equity=db,crypto=root/'missing',at=AT)
            source=result['sources'][0]
            self.assertEqual(result['export_version'],4)
            self.assertEqual(result['status'],'PARTIAL_SOURCE_EXPORT')
            self.assertEqual(source['status'],'SOURCE_ERROR')
            self.assertEqual(source['sqlite_errorname'],'SQLITE_INTERRUPT')
            self.assertTrue(source['partial_read'])
            self.assertTrue(source['truncated'])
            self.assertTrue(source['scan_limited'])
            self.assertEqual(source['sequence_slots_scanned'],2)
            self.assertEqual(source['exported'],1)
            self.assertEqual(sha256(db.read_bytes()).hexdigest(),before)
            with ZipFile(archive) as z:
                rows=[json.loads(r) for r in z.read('equity-saved-market-data.jsonl').splitlines()]
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['kind'],'QUOTE')
                self.assertNotIn(b'PRIVATE_',z.read('equity-saved-market-data.jsonl'))
            db.unlink()


    def test_existing_index_reaches_older_market_rows_past_unrelated_event_cap(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);path=root/'source';equity(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE INDEX capture_type_time ON capture_records(record_type,timestamp,sequence)')
                db.execute('INSERT INTO capture_records VALUES(?,?,?,?,?)',(1000000,'PAPER_FILL','TEST',AT.isoformat(),'{}'))
                plan=' '.join(str(r[-1]) for r in db.execute('EXPLAIN QUERY PLAN '+EQUITY_INDEX_QUERY,
                    ('MINUTE_BAR','z',9223372036854775807,(AT-timedelta(days=45)).isoformat(),AT.isoformat(),2000)))
                self.assertIn('capture_type_time',plan)
                self.assertNotIn('TEMP B-TREE',plan)
            before=sha256(path.read_bytes()).hexdigest()
            result,_=export_sources(root/'out',equity=path,crypto=root/'missing',at=AT)
            source=result['sources'][0]
            self.assertEqual(source['query_strategy'],'MARKET_TYPE_TIME_INDEX_V4')
            self.assertEqual(source['exported'],2)
            self.assertFalse(source['truncated'])
            self.assertEqual(source['type_rows_read'],{'MINUTE_BAR':1,'EXECUTION_PRICE_PATH':1})
            self.assertEqual(sha256(path.read_bytes()).hexdigest(),before)

    def test_index_keyset_handles_timestamp_ties_offsets_and_per_type_truncation(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);path=root/'source';equity(path)
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('CREATE INDEX capture_type_time ON capture_records(record_type,timestamp,sequence)')
                bar=db.execute("SELECT payload_json FROM capture_records WHERE record_type='MINUTE_BAR'").fetchone()[0]
                for i,stamp in enumerate([AT.isoformat(),AT.isoformat(),AT.astimezone(timezone(timedelta(hours=-4))).isoformat(),(AT+timedelta(days=1)).isoformat()],3):
                    db.execute('INSERT INTO capture_records VALUES(?,?,?,?,?)',(i,'MINUTE_BAR','TEST',stamp,bar))
            with patch('app.strategy_research.intraday_export.PAGE_SLOTS',1),patch('app.strategy_research.intraday_export.LIMIT',10):
                result,archive=export_sources(root/'out',equity=path,crypto=root/'missing',at=AT)
            self.assertEqual(result['sources'][0]['exported'],5)
            self.assertFalse(result['sources'][0]['truncated'])
            with patch('app.strategy_research.intraday_export.PAGE_SLOTS',1),patch('app.strategy_research.intraday_export.LIMIT',4):
                result,_=export_sources(root/'capped',equity=path,crypto=root/'missing',at=AT)
            self.assertEqual(result['sources'][0]['exported'],3)
            self.assertTrue(result['sources'][0]['truncated'])

    def test_index_interrupt_preserves_completed_page_and_source_failure(self):
        from app.read_only_sqlite import read_database as original_read
        @contextlib.contextmanager
        def interrupt_second(path):
            with original_read(path) as db:
                class Connection:
                    pages=0
                    def execute(self,query,args=()):
                        if query==EQUITY_INDEX_QUERY:
                            self.pages+=1
                            if self.pages==2:
                                db.set_progress_handler(lambda:1,1)
                        return db.execute(query,args)
                yield Connection()
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);path=root/'source';equity(path)
            with closing(sqlite3.connect(path)) as db,db:
                db.execute('CREATE INDEX capture_type_time ON capture_records(record_type,timestamp,sequence)')
            with patch('app.strategy_research.intraday_export.read_database',side_effect=interrupt_second),patch('app.strategy_research.intraday_export.PAGE_SLOTS',1):
                result,archive=export_sources(root/'out',equity=path,crypto=root/'missing',at=AT)
            source=result['sources'][0]
            self.assertEqual(source['exported'],1)
            self.assertEqual(source['sqlite_errorname'],'SQLITE_INTERRUPT')
            self.assertTrue(source['partial_read'])
            self.assertEqual(result['status'],'PARTIAL_SOURCE_EXPORT')
            with ZipFile(archive) as z:
                self.assertIn('equity-saved-market-data.jsonl',z.namelist())
            path.unlink()


if __name__=='__main__':unittest.main()

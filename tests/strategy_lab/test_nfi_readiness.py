from datetime import UTC,datetime,timedelta
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
import json

from app.strategy_lab.nfi_readiness import primary_activity, source_snapshot, runtime_freshness
from app.strategy_lab.nfi_forward_engine import Evidence, refresh_universe, close_capture, preflight
from tests.strategy_lab import test_nfi_forward_paper as fixtures


class ReadinessTests(unittest.TestCase):
    def test_activity_counts_absent_intervals_and_does_not_retimestamp(self):
        now=720*300+301
        rows=[[i*300000,1,2,.5,1,1] for i in range(720)]
        self.assertTrue(primary_activity(rows,now)['eligible'])
        before=rows[0].copy()
        rows=[r for i,r in enumerate(rows) if not (500<=i<580)]
        result=primary_activity(rows,now)
        self.assertEqual(result['reason'],'SOURCE_EMPTY_CANDLE_LIMIT')
        self.assertEqual(result['zero_volume_or_absent_288'],80)
        self.assertEqual(rows[0],before)
        self.assertEqual(primary_activity(rows,now+3600)['reason'],'STALE_PRIMARY')

    def test_source_buy_and_missing_indicators_are_distinct_from_freshness(self):
        import pandas as pd
        row=dict(date=pd.Timestamp('2026-01-01',tz='UTC'),enter_long=0,num_empty_288=0,
                 protections_long_global=True,**{k:30. for k in ('RSI_3','RSI_3_15m','RSI_3_1h','RSI_3_4h','RSI_3_1d')})
        frame=pd.DataFrame([row]);before=frame.copy(deep=True)
        self.assertEqual(source_snapshot(frame)['state'],'NO_SOURCE_BUY_SIGNAL')
        pd.testing.assert_frame_equal(frame,before)
        frame.loc[0,'enter_long']=1
        self.assertEqual(source_snapshot(frame)['state'],'SOURCE_BUY_SIGNAL')
        frame.loc[0,'enter_long']=0;frame.loc[0,'RSI_3_1h']=float('nan')
        self.assertEqual(source_snapshot(frame)['state'],'SOURCE_KEY_INDICATOR_NOT_READY')

    def test_closed_capture_not_stale_running_and_old_final_not_new_restart(self):
        now=datetime.now(UTC)
        report={'engine-status.json':{'updated_at':now.isoformat(),'recording_id':'one'},
                'capture-final.json':{'recording_id':'one','journal':{'closed':True,'updated_at':now.isoformat()}}}
        self.assertEqual(runtime_freshness(report,now)['state'],'STOPPED')
        report['engine-status.json']['recording_id']='two'
        self.assertEqual(runtime_freshness(report,now)['state'],'RECENT_CHECKPOINT')
        self.assertEqual(runtime_freshness(report,now+timedelta(minutes=3))['state'],'STALE_STATUS')

    def test_compact_status_does_not_show_a_stopped_writer_as_alive(self):
        import io
        from contextlib import redirect_stdout
        from app.strategy_lab.nfi_forward_status import main
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);now=datetime.now(UTC).isoformat()
            (work/'engine-status.json').write_text(json.dumps(dict(updated_at=now,
                fresh_ready_pairs=40,capture=dict(writer_alive=True))))
            (work/'capture-final.json').write_text(json.dumps(dict(journal=dict(
                updated_at=now,closed=True,writer_alive=False,evidence_complete=True))))
            output=io.StringIO()
            with patch('sys.argv',['status','--work',str(work),'--compact']),redirect_stdout(output):main()
            result=json.loads(output.getvalue())
            self.assertEqual(result['runtime_observation']['state'],'STOPPED')
            self.assertFalse(result['writer_alive'])
            self.assertIsNone(result['fresh_data_pairs'])


class UniverseTests(unittest.TestCase):
    setUp = fixtures.PaperTests.setUp
    fake_exchange = fixtures.PaperTests.fake_exchange

    def test_saved_selection_stays_fixed_until_explicit_refresh(self):
        with patch('ccxt.kraken',self.fake_exchange()):
            from app.strategy_lab.nfi_forward_engine import preflight
            preflight(self.work)
        before=(self.work/'resolved-config.json').read_bytes()
        token='1'*32
        with patch('app.strategy_lab.nfi_forward_engine.preflight',side_effect=RuntimeError('synthetic network failure')):
            with self.assertRaises(RuntimeError):refresh_universe(self.work,token)
        self.assertEqual(before,(self.work/'resolved-config.json').read_bytes())
        self.assertFalse((self.work/('universe-refresh-'+token+'.json')).exists())
        with patch('ccxt.kraken',self.fake_exchange()):refresh_universe(self.work,token)
        self.assertTrue((self.work/('universe-refresh-'+token+'.json')).exists())

    def test_refresh_preserves_closed_trades_wallet_and_is_one_time(self):
        import sqlite3
        with patch('ccxt.kraken',self.fake_exchange()):preflight(self.work)
        path=self.work/'nfi-paper.sqlite3';db=sqlite3.connect(path)
        db.execute('CREATE TABLE trades(is_open INTEGER)');db.execute('INSERT INTO trades VALUES(0)')
        db.execute('CREATE TABLE orders(ft_is_open INTEGER)');db.execute('INSERT INTO orders VALUES(0)')
        db.execute('CREATE TABLE wallet(balance INTEGER)');db.execute('INSERT INTO wallet VALUES(10234)')
        db.commit();db.close();before=path.read_bytes()
        with patch('ccxt.kraken',self.fake_exchange()):refresh_universe(self.work,'3'*32)
        self.assertEqual(before,path.read_bytes())
        with patch('app.strategy_lab.nfi_forward_engine.preflight',side_effect=AssertionError('repeat refresh')):
            refresh_universe(self.work,'3'*32)

    def test_new_selection_skips_source_activity_failures(self):
        base=self.fake_exchange()
        class Public(base):
            def fetch_ohlcv(self,pair,tf,limit):
                rows=super().fetch_ohlcv(pair,tf,limit)
                if pair=='S0/USD' and tf=='5m':
                    for row in rows[-100:]:row[5]=0
                return rows
        with patch('ccxt.kraken',Public):report=preflight(self.work)
        self.assertNotIn('S0/USD',report['selected_pairs'])
        self.assertEqual(len(report['selected_pairs']),11)
        self.assertEqual(report['primary_activity']['S0/USD']['reason'],'SOURCE_EMPTY_CANDLE_LIMIT')

    def test_native_cleanup_failure_still_closes_recording(self):
        from types import SimpleNamespace
        evidence=Evidence(self.work,'a'*64)
        def fail():raise RuntimeError('synthetic cleanup failure')
        with self.assertRaises(RuntimeError):close_capture(self.work,SimpleNamespace(exit=fail),evidence)
        final=json.loads((self.work/'capture-final.json').read_text())
        self.assertTrue(final['journal']['closed'])
        self.assertFalse(final['journal']['writer_alive'])
        self.assertEqual(final['cleanup_error'],'RuntimeError')

    def test_refresh_refuses_open_positions_without_modifying_selection(self):
        import sqlite3
        from app.strategy_lab.nfi_forward_engine import preflight
        with patch('ccxt.kraken',self.fake_exchange()):preflight(self.work)
        db=sqlite3.connect(self.work/'nfi-paper.sqlite3')
        db.execute('CREATE TABLE trades(is_open INTEGER)');db.execute('INSERT INTO trades VALUES(1)');db.commit();db.close()
        before=(self.work/'resolved-config.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'NO_OPEN_POSITIONS'):refresh_universe(self.work,'2'*32)
        self.assertEqual(before,(self.work/'resolved-config.json').read_bytes())


if __name__=='__main__':unittest.main()

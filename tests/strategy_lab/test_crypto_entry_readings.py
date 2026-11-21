from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile
from app.strategy_lab.crypto_entry_readings import canonical,conditions,collect_worker,collect


class EntryReadingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.work=self.root/'e0';self.work.mkdir();(self.work/'strategies').mkdir()
        (self.work/'strategies/A.py').write_text('# compiled strategy')
        (self.work/'config.json').write_text('{"dry_run":true}')
        self.baseline=sha256((self.work/'config.json').read_bytes()).hexdigest()
        self.name='recording-20261109T220700Z-'+'a'*32;self.rec=self.work/self.name;self.rec.mkdir()
        (self.work/'recording-session.json').write_text(json.dumps(dict(session='/work/'+self.name)))
        (self.work/'engine-status.json').write_text(json.dumps(dict(recording_id=self.name,state='running',fresh_ready_pairs=28)))
        self.rows=[]
        self.source=dict(candle_at='2026-11-09T23:50:00+00:00',state='NO_SOURCE_BUY_SIGNAL',buy=0,
            entry_inputs=dict(close=95,ema_8=100,ema_16=100,sma_15=100,rsi=30,rsi_fast=10,rsi_slow=20,cti=-.9,EWO=5),
            entry_parameters=dict(buy_ema_low=.942,buy_ema_high=1,buy_rsi=40,buy_rsi_fast=45,buy_ewo=0,
                buy_sma15_32=.942,buy_rsi_fast_32=46,buy_rsi_32=20,buy_cti_32=-.86))
        previous=deepcopy(self.source);previous['candle_at']='2026-11-09T23:45:00+00:00';previous['entry_inputs']['rsi_slow']=30
        self.append('FLEET_NATIVE_SIGNAL',dict(pair='BTC/USD',source_readiness=previous))
        self.append('FLEET_NATIVE_CANDLES',dict(pair='BTC/USD',timeframe='5m',rows=[dict(date='2026-11-09T23:50:00+00:00',open=95,high=96,low=94,close=95,volume=0)]))
        self.append('FLEET_NATIVE_SIGNAL',dict(pair='BTC/USD',source_readiness=self.source))
        self.finish()

    def append(self,kind,payload):
        row=dict(sequence=len(self.rows)+1,baseline_sha256=self.baseline,kind=kind,payload=payload)
        row['sha256']=sha256(canonical(row)).hexdigest();self.rows.append(row)

    def finish(self):
        self.path=self.rec/'events-00001.jsonl'
        self.path.write_bytes(b''.join(canonical(r)+b'\n' for r in self.rows))
        (self.rec/'journal-status.json').write_text(json.dumps(dict(baseline_sha256=self.baseline,
            counts={'written':len(self.rows)},updated_at='2026-11-09T23:51:00+00:00')))

    def test_reads_values_and_reports_failed_discount_without_mutating_input(self):
        before={str(p):p.read_bytes() for p in self.work.rglob('*') if p.is_file()}
        result,candles=collect_worker(self.work,'e0')
        c=result['latest_signals']['BTC/USD']['conditions']
        self.assertFalse(c['ewo_ema8_discount']['passed'])
        self.assertEqual(c['ewo_ema8_discount']['threshold'],94.19999999999999)
        self.assertTrue(c['pullback_slow_rsi_falling']['passed'])
        self.assertEqual(candles['BTC_USD-5m.json'][0]['volume'],0)
        self.assertFalse(result['full_hash_chain_verified'])
        for p,raw in before.items():self.assertEqual(Path(p).read_bytes(),raw)

    def test_missing_previous_or_gap_leaves_slow_rsi_unknown(self):
        c=conditions('e0',self.source)
        self.assertIsNone(c['pullback_slow_rsi_falling']['passed'])
        previous=deepcopy(self.source);previous['candle_at']='2026-11-09T23:30:00+00:00'
        self.assertIsNone(conditions('e0',self.source,previous)['pullback_slow_rsi_falling']['passed'])

    def test_partial_live_tail_and_post_checkpoint_rows_are_excluded(self):
        row=deepcopy(self.rows[-1]);row['sequence']=4;row['payload']['pair']='ETH/USD'
        row.pop('sha256');row['sha256']=sha256(canonical(row)).hexdigest()
        with self.path.open('ab') as f:f.write(canonical(row)+b'\n'+b'{partial')
        result,_=collect_worker(self.work,'e0')
        self.assertNotIn('ETH/USD',result['latest_signals'])
        self.assertEqual(result['partial_tail_lines_ignored'],1)

    def test_modified_row_and_config_refuse(self):
        original=self.path.read_bytes();self.path.write_bytes(original.replace(b'"close":95',b'"close":94',1))
        with self.assertRaisesRegex(ValueError,'HASH'):collect_worker(self.work,'e0')
        self.path.write_bytes(original);(self.work/'config.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'CONFIG'):collect_worker(self.work,'e0')

    def test_roc_clauses_and_missing_indicator_are_explicit(self):
        source=dict(entry_inputs=dict(close=95,sma200=90,roc3=-.02,volume=1))
        result=conditions('roc',source)
        self.assertTrue(result['trend_above_sma200']['passed'])
        self.assertFalse(result['three_hour_drop']['passed'])
        self.assertIsNone(conditions('roc',{})['three_hour_drop']['passed'])

    def test_small_export_works_without_network_or_other_workers(self):
        (self.root/'fleet.json').write_text(json.dumps(dict(live_trading_enabled=False,nfi_work=str(self.root/'nfi'))))
        output=self.root/'output'
        with patch('socket.socket',side_effect=AssertionError('NO_NETWORK')):
            report=collect(self.root,self.root/'addon',output)
        self.assertEqual(report['profiles']['e0']['rows_hash_verified'],3)
        self.assertIn('error',report['profiles']['nfi'])
        with zipfile.ZipFile(output/'crypto-entry-readings.zip') as z:
            self.assertIn('e0/candles/BTC_USD-5m.json',z.namelist())
            self.assertIn('entry-readings.json',z.namelist())
        self.assertLess((output/'crypto-entry-readings.zip').stat().st_size,4*1024*1024)

    def test_market_wide_retains_extra_hourly_pairs_and_excludes_five_minute(self):
        self.append('FLEET_NATIVE_CANDLES',dict(pair='AAVE/USD',timeframe='1h',rows=[dict(date='2026-11-09T23:00:00+00:00',open=95,high=96,low=94,close=95,volume=10)]))
        self.finish()
        _,small=collect_worker(self.work,'e0')
        _,wide=collect_worker(self.work,'e0',market_wide=True)
        self.assertNotIn('AAVE_USD-1h.json',small)
        self.assertIn('AAVE_USD-1h.json',wide)
        self.assertNotIn('BTC_USD-5m.json',wide)


if __name__=='__main__':unittest.main()

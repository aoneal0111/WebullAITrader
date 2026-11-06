import copy
from datetime import UTC,datetime,timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import tempfile

from app.strategy_lab import nfi_forward_paper as paper
from app.strategy_lab import nfi_forward_engine as engine
from app.strategy_lab.nfi_forward_status import ledger_status


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        fixture={
            'NostalgiaForInfinityX8.py':'# synthetic strategy fixture\n',
            'LICENSE':'Synthetic test license\n',
            'configs/exampleconfig.json':json.dumps(dict(max_open_trades=6,stake_amount='unlimited',
                timeframe='5m',exchange=dict(name='binance',key='',secret='',password='',
                    ccxt_config={},ccxt_async_config={}),api_server={'enabled':True},telegram={'enabled':True})),
            'configs/exampleconfig_secret.json':'{}',
            'configs/trading_mode-spot.json':'{"trading_mode":"spot"}',
            'configs/blacklist-kraken.json':'{"exchange":{"pair_blacklist":[]}}',
            'configs/pairlist-backtest-static-kraken-spot-usdt.json':json.dumps(dict(exchange=dict(name='kraken',
                pair_whitelist=['BTC/USD','ETH/USD']+[f'S{i}/USD' for i in range(10)]))),
        }
        hashes={}
        for name,text in fixture.items():
            p=self.source/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_text(text)
            hashes[name]=hashlib.sha256(p.read_bytes()).hexdigest()
        a=patch.object(paper,'HASHES',hashes);a.start();self.addCleanup(a.stop)
        b=patch.object(paper,'EXTRA_HASHES',{k:hashes[k] for k in paper.EXTRA_HASHES});b.start();self.addCleanup(b.stop)
        self.work=self.root/'work';paper.prepare(self.source,self.work)

    def test_source_and_config_preserved_as_native_paper(self):
        c,m=paper.verify(self.work)
        self.assertEqual(c['exchange']['name'],'kraken');self.assertEqual(c['stake_currency'],'USD')
        self.assertIs(c['dry_run'],True);self.assertEqual(c['stake_amount'],'unlimited')
        self.assertEqual(c['max_open_trades'],6);self.assertFalse(c['force_entry_enable'])
        self.assertEqual(m['source_commit'],paper.COMMIT)
        self.assertFalse((self.work/'nfi-paper.sqlite3').exists())

    def test_tampered_strategy_or_config_is_refused(self):
        (self.work/'strategies/NostalgiaForInfinityX8.py').write_text('changed')
        with self.assertRaisesRegex(ValueError,'PREPARED'):paper.verify(self.work)

    def test_bad_config_cannot_enable_real_trading_or_change_strategy(self):
        c=paper.verify(self.work)[0]
        for key,value in [('dry_run',False),('trading_mode','futures'),('timeframe','1m'),
                          ('max_open_trades',100),('force_entry_enable',True),('stoploss',-.01)]:
            changed=copy.deepcopy(c);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):paper.validate_config(changed)
        for key,value in [('key','private'),('name','binance'),('ccxt_config',{'httpsProxy':'http://elsewhere'})]:
            changed=copy.deepcopy(c);changed['exchange'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):paper.validate_config(changed)

    def fake_exchange(self,incomplete=False):
        pairs=paper.verify(self.work)[0]['exchange']['pair_whitelist']
        class Public:
            def __init__(self,*a,**k):pass
            def load_markets(self):return {p:dict(spot=True,active=True) for p in pairs}
            def fetch_tickers(self,allowed):return {p:{'quoteVolume':1000-i} for i,p in enumerate(allowed)}
            def fetch_ohlcv(self,pair,tf,limit):
                sec=paper.TIMEFRAMES[tf];end=int(datetime.now(UTC).timestamp())//sec*sec
                return [[(end-(720-i)*sec)*1000,1,2,.5,1,10] for i in range(20 if incomplete else 720)]
        return Public

    def test_public_preflight_and_seal_keep_universe_inside_source(self):
        with patch('ccxt.kraken',self.fake_exchange()):
            report=engine.preflight(self.work)
        self.assertEqual(report['status'],'PUBLIC_MARKET_READY')
        c=paper.selected_config(self.work)
        self.assertEqual(len(c['exchange']['pair_whitelist']),12)
        self.assertEqual(report['orders'],0)
        # A later start reuses the exact selection and checks current markets.
        before=(self.work/'resolved-config.json').read_bytes()
        with patch('ccxt.kraken',self.fake_exchange()):engine.preflight(self.work)
        self.assertEqual(before,(self.work/'resolved-config.json').read_bytes())
        changed=c;changed['stake_amount']=100
        paper.write(self.work/'resolved-config.json',changed)
        with self.assertRaisesRegex(ValueError,'CHANGED'):paper.selected_config(self.work)

    def test_missing_current_history_never_creates_ready_universe(self):
        with patch('ccxt.kraken',self.fake_exchange(True)),self.assertRaisesRegex(ValueError,'WARMUP'):
            engine.preflight(self.work)
        self.assertFalse((self.work/'resolved-config.json').exists())
        self.assertEqual(json.loads((self.work/'market-preflight.json').read_text())['status'],'FAILED')

    def test_environment_cannot_turn_dry_run_off(self):
        with patch.dict('os.environ',{'FREQTRADE__DRY_RUN':'false','FREQTRADE__EXCHANGE__KEY':'secret'}):
            engine.clean_environment()
            import os
            self.assertNotIn('FREQTRADE__DRY_RUN',os.environ)
            self.assertNotIn('FREQTRADE__EXCHANGE__KEY',os.environ)

    def bot(self,now):
        import pandas as pd
        frames={}
        for tf,seconds in paper.TIMEFRAMES.items():
            end=int(now.timestamp())//seconds*seconds-seconds
            dates=pd.to_datetime([end-(709-i)*seconds for i in range(710)],unit='s',utc=True)
            frames[tf]=pd.DataFrame(dict(date=dates,open=1.,high=2.,low=.5,close=1.,volume=10.))
        calls=[]
        def entry(pair,stake,*a,**kw):calls.append((pair,stake));return True
        api=SimpleNamespace(create_order=lambda:None,cancel_order=lambda:None)
        exchange=SimpleNamespace(_api=api,_api_async=copy.copy(api),
            get_tickers=lambda:{'BTC/USD':{'bid':1.,'ask':1.01},'UNRELATED/USD':{'bid':2.}},
            fetch_ticker=lambda pair:{'bid':1.,'ask':1.01},fetch_l2_order_book=lambda pair:{'bids':[[1,2]],'asks':[[1.01,2]]})
        dp=SimpleNamespace(ohlcv=lambda pair,tf,copy=False:frames[tf],
            get_analyzed_dataframe=lambda pair,tf:(frames[tf],now))
        return SimpleNamespace(config={'dry_run':True},dataprovider=dp,strategy=SimpleNamespace(startup_candle_count=710),
            exchange=exchange,active_pair_whitelist=['BTC/USD'],execute_entry=entry,
            execute_trade_exit=lambda *a:True),calls,frames

    def test_entry_capture_pause_restart_and_exits_are_independent(self):
        now=datetime.now(UTC);bot,calls,frames=self.bot(now)
        evidence=engine.Evidence(self.work,'a'*64);self.addCleanup(evidence.journal.close)
        evidence.attach(bot,self.work)
        self.assertFalse(bot.execute_entry('BTC/USD',100)) # capture has not begun
        evidence.capture(bot,now)
        self.assertIsNone(evidence.error)
        self.assertTrue(bot.execute_entry('BTC/USD',100))
        (self.work/'PAUSE_NEW_ENTRIES').touch()
        self.assertFalse(bot.execute_entry('BTC/USD',100,trade=SimpleNamespace(id=1)))
        self.assertTrue(bot.execute_trade_exit())
        (self.work/'PAUSE_NEW_ENTRIES').unlink()
        self.assertTrue(bot.execute_entry('BTC/USD',100))
        evidence.error='CAPTURE_FAILURE'
        self.assertFalse(bot.execute_entry('BTC/USD',100))
        self.assertTrue(bot.execute_trade_exit());self.assertEqual(len(calls),2)
        with self.assertRaisesRegex(RuntimeError,'REAL_EXCHANGE_WRITES_DISABLED'):bot.exchange._api.create_order()
        evidence.journal.close()
        self.assertTrue(evidence.journal.status()['evidence_complete'])

    def test_stale_and_insufficient_candles_refuse_entries(self):
        now=datetime.now(UTC);bot,_,frames=self.bot(now)
        self.assertTrue(engine.pair_ready(bot,'BTC/USD',now))
        frames['5m']=frames['5m'].iloc[-100:]
        self.assertFalse(engine.pair_ready(bot,'BTC/USD',now))
        bot,_,frames=self.bot(now-timedelta(hours=2))
        self.assertFalse(engine.pair_ready(bot,'BTC/USD',now))

    def test_invalid_candle_shape_refuses_without_stopping_exit_management(self):
        now=datetime.now(UTC);bot,_,frames=self.bot(now)
        frames['5m']=frames['5m'].drop(columns=['date'])
        self.assertFalse(engine.pair_ready(bot,'BTC/USD',now))
        self.assertTrue(bot.execute_trade_exit())

    def test_provider_capture_has_no_extra_calls_or_unrelated_tickers(self):
        now=datetime.now(UTC);bot,_,_=self.bot(now)
        evidence=engine.Evidence(self.work,'a'*64);self.addCleanup(evidence.journal.close)
        evidence.attach(bot,self.work)
        first=bot.exchange.get_tickers();second=bot.exchange.get_tickers()
        self.assertEqual(first,second)
        evidence.journal.close()
        lines=[json.loads(line) for p in evidence.root.glob('events-*.jsonl') for line in p.read_text().splitlines()]
        rows=[r for r in lines if r['kind']=='NFI_NATIVE_PUBLIC_RESPONSE']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['payload']['subject'],'BTC/USD')

    def test_raw_ohlcv_is_captured_before_native_normalization(self):
        import asyncio
        now=datetime.now(UTC);bot,_,_=self.bot(now);calls=[]
        raw=[[1000,1.,2.,.5,1.,10.],[601000,1.,2.,.5,1.,0.]]
        async def fetch(pair,tf,*a,**kw):calls.append((pair,tf));return raw
        bot.exchange._api_async.fetch_ohlcv=fetch
        evidence=engine.Evidence(self.work,'a'*64);self.addCleanup(evidence.journal.close)
        evidence.attach(bot,self.work)
        result=asyncio.run(bot.exchange._api_async.fetch_ohlcv('BTC/USD','5m'))
        self.assertIs(result,raw);self.assertEqual(calls,[('BTC/USD','5m')])
        evidence.journal.close()
        rows=[json.loads(line) for p in evidence.root.glob('events-*.jsonl') for line in p.read_text().splitlines()]
        event=next(r for r in rows if r['kind']=='NFI_RAW_PUBLIC_OHLCV')
        self.assertEqual(event['payload']['rows'],raw)
        self.assertFalse(event['payload']['provider_timestamps_modified'])

    def test_status_does_not_create_or_write_a_database(self):
        self.assertFalse(ledger_status(self.work)['ledger_present'])
        import sqlite3
        path=self.work/'nfi-paper.sqlite3';db=sqlite3.connect(path)
        db.execute('CREATE TABLE trades (id INTEGER,pair TEXT,amount REAL,open_rate REAL,is_open INTEGER,close_profit_abs REAL)')
        db.executemany('INSERT INTO trades VALUES (?,?,?,?,?,?)',[(1,'BTC/USD',1,100,0,5),(2,'ETH/USD',1,100,0,-2),(3,'ETH/USD',1,100,1,None)])
        db.commit();db.close();before=path.read_bytes()
        result=ledger_status(self.work)
        self.assertEqual(result['closed_trades'],2);self.assertEqual(result['closed_win_rate'],.5)
        self.assertEqual(result['closed_lifecycle_net_usd'],3);self.assertEqual(result['open_positions'],1)
        self.assertEqual(path.read_bytes(),before)



    def test_export_preserves_original_and_reconciles_native_snapshot(self):
        import sqlite3,zipfile
        from app.strategy_lab.nfi_forward_status import export_review
        path=self.work/'nfi-paper.sqlite3'
        db=sqlite3.connect(path)
        db.execute('CREATE TABLE trades (id INTEGER,pair TEXT,amount REAL,open_rate REAL,is_open INTEGER,close_profit_abs REAL,realized_profit REAL)')
        db.executemany('INSERT INTO trades VALUES (?,?,?,?,?,?,?)',[(1,'BTC/USD',1,100,0,5,5),(2,'ETH/USD',1,100,1,None,2)])
        db.commit();db.close();before=path.read_bytes()
        with patch('app.strategy_lab.nfi_forward_paper.selected_config',return_value={}):
            archive=export_review(self.work,self.root/'review')
        self.assertEqual(before,path.read_bytes())
        with zipfile.ZipFile(archive) as z:
            report=json.loads(z.read('review-summary.json'))
            self.assertEqual(report['closed_lifecycle_net_usd'],5)
            self.assertEqual(report['open_lifecycle_realized_usd'],2)
            self.assertEqual(report['open_positions'],1)
            self.assertIn('nfi-paper.sqlite3',z.namelist())


class NativeProcessLockTests(unittest.TestCase):
    def test_second_process_cannot_open_same_paper_worker(self):
        try:import fcntl
        except ImportError:self.skipTest('native worker runs inside Linux Docker')
        with tempfile.TemporaryDirectory() as root:
            work=Path(root)
            lock=(work/'engine.lock').open('a')
            try:
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError,'ALREADY_RUNNING'):
                    engine.run(work)
            finally:lock.close()


if __name__=='__main__':unittest.main()

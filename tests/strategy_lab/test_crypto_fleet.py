"""Offline source compatibility, prefix invariance and native order contracts."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from app.strategy_lab.crypto_fleet import PROFILES, prepare, digest, summary


HAS_NATIVE = importlib.util.find_spec('freqtrade') is not None
SOURCE = os.environ.get('ATLAS_FLEET_TEST_SOURCES')


@unittest.skipUnless(HAS_NATIVE and SOURCE, 'Pinned native runtime and verified source cache required')
class FleetNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.strategy_lab.crypto_fleet_runtime import check
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)/'fleet'
        prepare(cls.root,Path(SOURCE))
        for key in PROFILES:check(cls.root/key)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def load(self,key):
        from app.strategy_lab.crypto_fleet_runtime import resolve
        from freqtrade.persistence import init_db
        init_db('sqlite://')
        return resolve(self.root/key)

    def frames(self,key,now=None):
        import numpy as np
        import pandas as pd
        from app.strategy_lab.crypto_fleet_runtime import frames_for
        now=now or datetime.now(UTC)
        out={}
        for tf,seconds in frames_for(key).items():
            end=int(now.timestamp())//seconds*seconds-seconds
            n=np.arange(740,dtype=float)
            close=100+2*np.sin(n/7)+np.sin(n/19)+n*.005
            frame=pd.DataFrame(dict(date=pd.to_datetime([end-(739-i)*seconds for i in range(740)],unit='s',utc=True),
                open=close-.1,high=close+.4,low=close-.4,close=close,
                volume=np.where(n%17==0,1000.,100.)))
            out[tf]=frame
        return out

    def provider(self,frames,pairs):
        return SimpleNamespace(current_whitelist=lambda:pairs,
            get_pair_dataframe=lambda pair,timeframe,**kw:frames[timeframe].copy(deep=True),
            ohlcv=lambda pair,tf,copy=False:frames[tf],
            get_analyzed_dataframe=lambda pair,tf:(frames[tf],datetime.now(UTC)))

    def test_all_five_are_native_paper_and_wallets_are_independent(self):
        for key in PROFILES:
            with self.subTest(key=key):
                _,config,strategy,profile=self.load(key)
                self.assertIs(config['dry_run'],True)
                self.assertFalse(strategy.can_short)
                self.assertEqual(strategy.startup_candle_count,640)
                self.assertEqual(config['stake_amount'],'unlimited')
                self.assertIn(str(self.root/key),config['db_url'])
                self.assertFalse(profile['published_performance_applies'])
        self.assertEqual(summary(self.root)['profiles']['wave']['closed_trades'],0)
        self.assertFalse(any(self.root.glob('*/paper.sqlite3')))

    def test_current_source_signals_are_prefix_invariant_after_warmup(self):
        import pandas as pd
        from app.strategy_lab.nfi_runtime_preflight import disconnected
        for key in PROFILES:
            with self.subTest(key=key), disconnected():
                _,config,strategy,profile=self.load(key)
                frames=self.frames(key);tf=profile['timeframe'];pair=profile['pairs'][0]
                strategy.dp=self.provider(frames,profile['pairs'])
                def calculate(frame):
                    result=strategy.populate_indicators(frame.copy(deep=True),{'pair':pair})
                    result=strategy.populate_entry_trend(result,{'pair':pair})
                    return strategy.populate_exit_trend(result,{'pair':pair})
                full=calculate(frames[tf]);prefix=calculate(frames[tf].iloc[:720])
                pd.testing.assert_frame_equal(full.iloc[640:720],prefix.iloc[640:720],
                    check_exact=False,rtol=1e-10,atol=1e-10)

    def test_bb_roc_uses_hourly_candles_without_primary_index_alignment(self):
        import pandas as pd
        import talib.abstract as ta
        _,_,strategy,profile=self.load('bb')
        frames=self.frames('bb');strategy.dp=self.provider(frames,profile['pairs'])
        informative=strategy.informative_1h_indicators(frames['5m'].iloc[:100],{'pair':profile['pairs'][0]})
        pd.testing.assert_series_equal(informative['roc'],ta.ROC(frames['1h'],timeperiod=9),check_names=False)
        self.assertTrue(informative['roc'].iloc[640:].notna().all())

    def test_tampering_cannot_enable_real_orders_even_with_rehashed_config(self):
        from app.strategy_lab.crypto_fleet_runtime import verify
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            work=Path(tmp)/'profile';shutil.copytree(self.root/'e0',work)
            cfg=json.loads((work/'config.json').read_text());cfg['dry_run']=False
            (work/'config.json').write_text(json.dumps(cfg))
            profile=json.loads((work/'profile.json').read_text());profile['config_sha256']=digest(work/'config.json')
            (work/'profile.json').write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError,'SPOT_PAPER'):verify(work)
            cfg['dry_run']=True;cfg['exchange']['key']='not-a-real-key'
            (work/'config.json').write_text(json.dumps(cfg))
            profile['config_sha256']=digest(work/'config.json');(work/'profile.json').write_text(json.dumps(profile))
            with self.assertRaisesRegex(ValueError,'SPOT_PAPER'):verify(work)

    def test_causal_crsi_keeps_warmup_missing_and_original_bytes_untouched(self):
        _,_,strategy,_=self.load('crsi')
        frame=strategy.populate_indicators(self.frames('crsi')['4h'],{'pair':'BTC/USD'})
        self.assertTrue(frame['sma_200'].iloc[:199].isna().all())
        original=(self.root/'crsi/upstream/strategy.py').read_text()
        self.assertIn("df['sma_200'].bfill()",original)
        generated=(self.root/'crsi/strategies/FleetCRSIReference.py').read_text()
        self.assertNotIn("df['sma_200'].bfill()",generated)

    def test_wave_frozen_plan_has_restart_stable_stop_and_target(self):
        _,_,strategy,_=self.load('wave')
        tag='WAVE:'+float(99).hex()+':'+float(102).hex()
        trade=SimpleNamespace(enter_tag=tag)
        self.assertTrue(strategy.confirm_trade_entry('SOL/USD','limit',1,100,'GTC',tag))
        self.assertFalse(strategy.confirm_trade_entry('SOL/USD','limit',1,103,'GTC',tag))
        self.assertEqual(strategy.custom_exit('SOL/USD',trade,None,98,-.02),'WAVE_SOURCE_STOP')
        self.assertEqual(strategy.custom_exit('SOL/USD',trade,None,103,.03),'WAVE_FIRST_TARGET')
        self.assertGreater(strategy.custom_stoploss('SOL/USD',trade,None,100,0),0)

    def test_native_buy_reaches_one_unfilled_order_for_each_profile(self):
        from freqtrade.freqtradebot import FreqtradeBot
        from freqtrade.enums import TradingMode
        from freqtrade.persistence import Trade, init_db
        from app.strategy_lab.crypto_fleet_runtime import ready, frames_for, snapshot
        from app.strategy_lab.nfi_forward_engine import Evidence
        from app.strategy_lab.nfi_runtime_preflight import disconnected
        for key in PROFILES:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp, disconnected():
                _,config,strategy,profile=self.load(key)
                init_db('sqlite:///'+str(Path(tmp)/'test-only.sqlite3'))
                bot=FreqtradeBot.__new__(FreqtradeBot);bot.config=config;bot.strategy=strategy;bot.trading_mode=TradingMode.SPOT
                frames=self.frames(key);tf=profile['timeframe'];now=datetime.now(UTC)
                # Explicit synthetic decision fixture: this verifies native
                # plumbing, not an upstream source setup or profitable fill.
                primary=frames[tf];primary['enter_long']=1;primary['exit_long']=0
                primary['close']=100.;primary['enter_tag']='SYNTHETIC_ORDER_PATH_ONLY'
                if key=='wave':primary['enter_tag']='WAVE:'+float(99).hex()+':'+float(102).hex()
                pair=profile['pairs'][0];bot.active_pair_whitelist=[pair]
                bot.dataprovider=self.provider(frames,[pair]);strategy.dp=bot.dataprovider
                bot.wallets=Mock();bot.wallets.get_trade_stake_amount.return_value=100.
                bot.wallets.get_available_stake_amount.return_value=10000.
                bot.wallets.validate_stake_amount.side_effect=lambda **kw:kw['stake_amount']
                exchange=bot.exchange=Mock();exchange.id='kraken';exchange.precisionMode=exchange.precision_mode_price=2
                exchange._api=SimpleNamespace(create_order=Mock());exchange._api_async=SimpleNamespace(create_order=Mock())
                for name,value in [('get_rate',100.),('get_min_pair_stake_amount',1.),('get_max_pair_stake_amount',10000.),
                    ('get_fee',.004),('get_pair_base_currency',pair.split('/')[0]),('get_precision_amount',8),
                    ('get_precision_price',2),('get_contract_size',1.)]:getattr(exchange,name).return_value=value
                exchange.create_order.return_value=dict(id='synthetic-only',symbol=pair,type='limit',side='buy',
                    status='open',price=100.,amount=1.,filled=0.,remaining=1.,cost=0.,timestamp=int(now.timestamp()*1000))
                bot._notify_enter=Mock()
                evidence=Evidence(Path(tmp),'a'*64,timeframes=frames_for(key),primary=tf,
                    ready=lambda bot,p,at:ready(bot,p,at,key),snapshot=snapshot,prefix='FLEET')
                try:
                    evidence.attach(bot,Path(tmp));evidence.capture(bot,now)
                    self.assertIsNone(evidence.error)
                    self.assertTrue(bot.create_trade(pair))
                    self.assertEqual(exchange.create_order.call_count,1)
                    trades=Trade.get_trades().all();self.assertEqual(len(trades),1)
                    self.assertEqual(trades[0].orders[0].filled,0)
                    with self.assertRaisesRegex(RuntimeError,'REAL_EXCHANGE_WRITES_DISABLED'):exchange._api.create_order()
                finally:evidence.journal.close();Trade.session.remove()

    def test_freshness_is_specific_to_primary_and_informative_timeframes(self):
        from app.strategy_lab.crypto_fleet_runtime import ready
        for key in PROFILES:
            frames=self.frames(key);now=datetime.now(UTC)
            bot=SimpleNamespace(config={'dry_run':True},dataprovider=self.provider(frames,['SOL/USD']))
            self.assertTrue(ready(bot,'SOL/USD',now,key))
            tf=PROFILES[key]['timeframe'];frames[tf]['date']-=timedelta(days=3)
            self.assertFalse(ready(bot,'SOL/USD',now,key))


if __name__=='__main__':unittest.main()

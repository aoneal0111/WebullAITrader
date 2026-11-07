import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,UTC,timedelta
from types import SimpleNamespace
from unittest.mock import Mock,patch
from app.strategy_lab.paper_candidate_addon import prepare
from app.strategy_lab.equity_daily_paper import database,cycle,status,parse


class EquityPaperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.work=Path(self.temp.name);self.db=database(self.work/'paper.sqlite3');self.now=datetime(2026,11,9,15,tzinfo=UTC)
        closes=[100+i*.2 for i in range(220)]+[141,139,138,137]
        start=datetime(2026,11,6)-timedelta(days=len(closes)-1)
        def bars(prices):return {(start+timedelta(days=i)).date().isoformat():dict(open=c+.1,high=c+1,low=c-1,close=c,volume=1000) for i,c in enumerate(prices)}
        self.rows={'SPY':bars([100+i*.2 for i in range(len(closes))]),'QQQ':bars(closes)}
        self.quotes={s:dict(price=137.,at=self.now.isoformat(),fresh_regular_session=True) for s in self.rows}

    def tearDown(self):self.db.close();self.temp.cleanup()

    def test_real_source_signal_creates_persisted_buys_and_restart_is_idempotent(self):
        result=cycle(self.db,self.rows,self.quotes,self.now)
        self.assertEqual(len(result['orders']),2)
        self.assertTrue(all(x['side']=='BUY' for x in result['orders']))
        self.db.close();self.db=database(self.work/'paper.sqlite3')
        self.assertFalse(cycle(self.db,self.rows,self.quotes,self.now)['orders'])
        self.assertTrue(all(x['simulated_orders']==1 for x in status(self.db).values()))
        self.assertTrue(all(x['closed_trades']==0 for x in status(self.db).values()))

    def test_pause_stale_price_closed_market_and_no_signal_are_distinct(self):
        paused=cycle(self.db,self.rows,self.quotes,self.now,paused=True)
        self.assertFalse(paused['orders']);self.assertIn('OPERATOR_PAUSE',paused['reasons'].values())
        for q in self.quotes.values():q['at']=(self.now-timedelta(minutes=3)).isoformat()
        stale=cycle(self.db,self.rows,self.quotes,self.now)
        self.assertFalse(stale['orders']);self.assertIn('STALE_PUBLIC_PRICE_OR_DAILY_HISTORY',stale['reasons'].values())
        for q in self.quotes.values():q.update(at=self.now.isoformat(),fresh_regular_session=False)
        self.assertFalse(cycle(self.db,self.rows,self.quotes,self.now)['orders'])
        self.rows['QQQ']=self.rows['SPY'];r=cycle(self.db,self.rows,self.quotes,self.now)
        self.assertIn('NO_BUY_SIGNAL',r['reasons'].values())

    def test_exits_continue_when_paused_and_benchmark_is_missing(self):
        cycle(self.db,self.rows,self.quotes,self.now)
        self.rows['QQQ']['2026-11-09']=dict(open=150,high=151,low=149,close=150,volume=1000)
        self.rows.pop('SPY')
        r=cycle(self.db,self.rows,self.quotes,self.now,paused=True)
        self.assertEqual(len(r['orders']),2);self.assertTrue(all(x['side']=='SELL' for x in r['orders']))
        for v in status(self.db).values():
            self.assertEqual(v['closed_trades'],1);self.assertFalse(v['open_positions'])

    def test_partial_daily_candle_is_ignored_and_weekend_cannot_be_fresh(self):
        raw=json.dumps({'chart':{'result':[{'timestamp':[datetime(2026,11,7,15,tzinfo=UTC).timestamp()],
            'meta':{'regularMarketPrice':100,'regularMarketTime':self.now.timestamp()},
            'indicators':{'quote':[dict(open=[1],high=[2],low=[1],close=[2],volume=[5])], 'adjclose':[{'adjclose':[2]}]}}]}})
        weekend=datetime(2026,11,7,15,tzinfo=UTC)
        bars,q=parse(raw,weekend);self.assertFalse(q['fresh_regular_session']);self.assertEqual(bars,{})

    def test_missing_benchmark_refuses_buys_without_killing_worker(self):
        self.rows.pop('SPY');r=cycle(self.db,self.rows,self.quotes,self.now)
        self.assertFalse(r['orders']);self.assertIn('BENCHMARK_DATE_MISMATCH',r['reasons'].values())

    def test_recording_failure_rolls_back_buy_and_cash_together(self):
        self.db.execute("CREATE TRIGGER fail_recording BEFORE INSERT ON observations BEGIN SELECT RAISE(FAIL,'TEST_RECORDING_FAILURE'); END")
        with self.assertRaisesRegex(Exception,'TEST_RECORDING_FAILURE'):
            cycle(self.db,self.rows,self.quotes,self.now)
        self.db.rollback()
        self.assertEqual(self.db.execute('SELECT count(*) FROM orders').fetchone()[0],0)
        self.assertTrue(all(r['cash']==10000 for r in status(self.db).values()))

    def test_public_preflight_keeps_provider_failures_independent(self):
        from app.strategy_lab.paper_candidate_addon import public_preflight
        from app.strategy_lab import equity_daily_paper as daily
        exchange=Mock();exchange.fetch_ohlcv.side_effect=RuntimeError('TEST_CRYPTO_NETWORK_FAILURE')
        with patch('ccxt.kraken',return_value=exchange),patch('app.strategy_lab.crypto_fleet_runtime.verify'),patch.object(daily,'fetch',return_value=b'fixture'),patch.object(daily,'parse',return_value=({str(i):{} for i in range(220)},dict(market_open=False))):
            r=public_preflight(self.work)
        self.assertFalse(r['workers']['crypto']['ready'])
        self.assertTrue(r['workers']['equity']['ready'])
        self.assertEqual(r['workers']['equity']['reason'],'WAIT_MARKET_OPEN')

    def test_simulated_wallets_do_not_borrow_each_others_cash(self):
        self.db.execute('UPDATE wallets SET cash=0 WHERE strategy="RSI2_SPY_REGIME"');self.db.commit()
        r=cycle(self.db,self.rows,self.quotes,self.now)
        self.assertEqual(len(r['orders']),1);self.assertEqual(r['orders'][0]['strategy'],'CONNORS_RSI2')


class NativeCryptoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'addon';prepare(self.root,'a'*40)
        from app.strategy_lab.crypto_fleet_runtime import check,resolve
        from freqtrade.persistence import init_db
        init_db('sqlite://')
        self.assertEqual(check(self.root/'crypto')['startup_candles'],200)
        _,self.config,self.strategy,_=resolve(self.root/'crypto')

    def tearDown(self):
        from freqtrade.persistence import Trade
        Trade.session.remove();self.temp.cleanup()

    def frame(self):
        import pandas as pd
        now=datetime.now(UTC);end=int(now.timestamp())//3600*3600-3600
        closes=[100.]*200+[200.]*36+[195.,190.,180.,170.]
        frame=pd.DataFrame(dict(date=pd.to_datetime([end-(239-i)*3600 for i in range(240)],unit='s',utc=True),
            open=closes,high=[c+1 for c in closes],low=[c-1 for c in closes],close=closes,volume=1000.))
        frame=self.strategy.populate_indicators(frame,{'pair':'BTC/USD'})
        frame=self.strategy.populate_entry_trend(frame,{'pair':'BTC/USD'})
        return self.strategy.populate_exit_trend(frame,{'pair':'BTC/USD'})

    def test_source_entry_prefix_and_frozen_atr_exit_geometry(self):
        import pandas as pd
        f=self.frame();self.assertEqual(int(f.iloc[-1]['enter_long']),1)
        raw=f.iloc[:-1][['date','open','high','low','close','volume']]
        part=self.strategy.populate_entry_trend(self.strategy.populate_indicators(raw,{}),{})
        pd.testing.assert_series_equal(f['enter_long'].iloc[:-1],part['enter_long'])
        tag=f.iloc[-1]['enter_tag'];trade=SimpleNamespace(enter_tag=tag,open_rate=170.,open_date_utc=datetime.now(UTC)-timedelta(hours=11))
        self.assertEqual(self.strategy.custom_exit('BTC/USD',trade,datetime.now(UTC),170,0),'ROC3_10_BAR_TIME_EXIT')
        self.assertTrue(self.strategy.confirm_trade_entry('BTC/USD','limit',1,170,'GTC',tag))
        self.strategy.config['dry_run']=False
        self.assertFalse(self.strategy.confirm_trade_entry('BTC/USD','limit',1,170,'GTC',tag))

    def test_native_buy_reaches_persisted_order_with_evidence_and_no_real_writes(self):
        from freqtrade.freqtradebot import FreqtradeBot
        from freqtrade.enums import TradingMode
        from freqtrade.persistence import Trade,init_db
        from app.strategy_lab.crypto_fleet_runtime import ready,frames_for,snapshot
        from app.strategy_lab.nfi_forward_engine import Evidence
        from app.strategy_lab.nfi_runtime_preflight import disconnected
        init_db('sqlite:///'+str(self.root/'test-only.sqlite3'))
        now=datetime.now(UTC);frame=self.frame();bot=FreqtradeBot.__new__(FreqtradeBot)
        bot.config=self.config;bot.strategy=self.strategy;bot.trading_mode=TradingMode.SPOT
        bot.dataprovider=SimpleNamespace(ohlcv=lambda *a,**kw:frame,get_analyzed_dataframe=lambda *a:(frame,now))
        bot.active_pair_whitelist=['BTC/USD'];self.strategy.dp=bot.dataprovider
        bot.wallets=Mock();bot.wallets.get_trade_stake_amount.return_value=100.;bot.wallets.get_available_stake_amount.return_value=10000.
        bot.wallets.validate_stake_amount.side_effect=lambda **kw:kw['stake_amount']
        exchange=bot.exchange=Mock();exchange.id='kraken';exchange.precisionMode=exchange.precision_mode_price=2
        exchange._api=SimpleNamespace(create_order=Mock());exchange._api_async=SimpleNamespace(create_order=Mock())
        for name,value in [('get_rate',170.),('get_min_pair_stake_amount',1.),('get_max_pair_stake_amount',10000.),('get_fee',.008),('get_pair_base_currency','BTC'),('get_precision_amount',8),('get_precision_price',2),('get_contract_size',1.)]:getattr(exchange,name).return_value=value
        exchange.create_order.return_value=dict(id='test-paper-only',symbol='BTC/USD',type='limit',side='buy',status='open',price=170.,amount=.5,filled=0.,remaining=.5,cost=0.,timestamp=int(now.timestamp()*1000))
        bot._notify_enter=Mock()
        evidence=Evidence(self.root,'a'*64,timeframes=frames_for('roc'),primary='1h',ready=lambda b,p,n:ready(b,p,n,'roc'),snapshot=snapshot,prefix='FLEET')
        try:
            with disconnected():
                evidence.attach(bot,self.root);evidence.capture(bot,now)
                self.assertTrue(ready(bot,'BTC/USD',now,'roc'));self.assertTrue(bot.create_trade('BTC/USD'))
                self.assertEqual(exchange.create_order.call_count,1);self.assertEqual(len(Trade.get_trades().all()),1)
                self.assertEqual(Trade.get_trades().first().orders[0].filled,0)
                with self.assertRaisesRegex(RuntimeError,'REAL_EXCHANGE_WRITES_DISABLED'):exchange._api.create_order()
        finally:evidence.journal.close()

    def test_native_dry_exchange_fills_crossed_buy_and_preserves_unfilled_buy(self):
        from freqtrade.exchange import Exchange
        exchange=SimpleNamespace(_dry_run_open_orders={},
            _contracts_to_amount=lambda p,a:a, _amount_to_contracts=lambda p,a:a,
            amount_to_precision=lambda p,a:a, exchange_has=lambda n:True,
            fetch_l2_order_book=lambda *a:{'asks':[[170.,10.]],'bids':[[169.9,10.]]},
            get_fee=lambda *a,**kw:.008,get_pair_quote_currency=lambda p:'USD')
        for name in ('_dry_is_price_crossed','check_dry_limit_order_filled','add_dry_order_fee'):
            setattr(exchange,name,getattr(Exchange,name).__get__(exchange))
        waiting=Exchange.create_dry_run_order(exchange,'BTC/USD','limit','buy',.5,169.,1.)
        filled=Exchange.create_dry_run_order(exchange,'BTC/USD','limit','buy',.5,170.,1.)
        self.assertEqual(waiting['status'],'open');self.assertEqual(waiting['filled'],0)
        self.assertEqual(filled['status'],'closed');self.assertEqual(filled['filled'],.5)
        self.assertAlmostEqual(filled['fee']['cost'],.68)

    def test_native_freshness_is_200_hourly_bars_and_stale_stays_blocked(self):
        from app.strategy_lab.crypto_fleet_runtime import ready
        f=self.frame();bot=SimpleNamespace(config=self.config,dataprovider=SimpleNamespace(ohlcv=lambda *a,**kw:f,get_analyzed_dataframe=lambda *a:(f,datetime.now(UTC))))
        self.assertTrue(ready(bot,'BTC/USD',datetime.now(UTC),'roc'))
        f['date']-=timedelta(days=2);self.assertFalse(ready(bot,'BTC/USD',datetime.now(UTC),'roc'))


if __name__=='__main__':unittest.main()

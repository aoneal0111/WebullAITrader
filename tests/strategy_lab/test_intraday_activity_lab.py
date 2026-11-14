import unittest
import math
import pandas as pd
from app.strategy_lab.intraday_activity_lab import features,active_rules,daily_metrics,gate,FAMILIES
from app.strategy_lab.broad_strategy_lab import Case


class ActivityTests(unittest.TestCase):
    def test_all_new_rules_are_prefix_invariant(self):
        close=[100+i*.05+6*math.sin(i/4) for i in range(260)]
        f=pd.DataFrame(dict(date=pd.date_range('2020-01-01',periods=260,freq='5min',tz='UTC'),open=close,high=[c+.3 for c in close],low=[c-.3 for c in close],close=close,volume=[3000 if i%3==0 else 100 for i in range(260)]))
        a=features(f);b=features(f.iloc[:240])
        count=0
        for family,params in FAMILIES.items():
            for param in params:
                x=active_rules(a,Case(family,param));y=active_rules(b,Case(family,param))
                pd.testing.assert_series_equal(x['buy'].iloc[:240],y['buy'])
                pd.testing.assert_series_equal(x['sell'].iloc[:240],y['sell'])
                self.assertFalse(x['buy'].iloc[:200].any());count+=int(x['buy'].sum())
        self.assertGreater(count,0)

    def test_daily_activity_counts_fills_not_repeated_signals_and_includes_idle_days(self):
        dates=list(pd.date_range('2020-01-01',periods=3,freq='D',tz='UTC'))
        r=dict(entries=2,marked_net=40,events=[{'at':str(dates[0]),'action':'BUY'}]*2,
               curve=[dict(at=str(dates[0]),equity=10020),dict(at=str(dates[2]),equity=10040)])
        a=daily_metrics(r,dates)
        self.assertEqual(a['calendar_days'],3);self.assertEqual(a['entry_active_days'],1)
        self.assertEqual(a['entries_per_day'],2/3);self.assertEqual(a['net_after_overhead'],34)
        self.assertEqual(sum(x['marked_net'] for x in a['daily']),40)

    def test_equity_metrics_exclude_market_closed_weekend_days(self):
        from app.strategy_lab.equity_activity_lab import session_metrics
        calendar=['2024-03-01','2024-03-04']
        result=dict(entries=1,marked_net=30,events=[{'at':'2024-03-01','action':'BUY'}],
            curve=[{'at':'2024-03-01','equity':10010},{'at':'2024-03-04','equity':10030}])
        a=session_metrics(result,calendar)
        self.assertEqual(a['trading_sessions'],2);self.assertEqual(a['entries_per_day'],.5)
        self.assertEqual(a['net_after_overhead'],26)

    def test_optional_source_curve_preserves_original_results(self):
        from datetime import date,timedelta
        from app.strategy_research.source_candidate_lab import replay
        prices=[100+i*.2 for i in range(220)]+[141,139,138,137,140,143,144,145]
        bars={(date(2020,1,1)+timedelta(days=i)).isoformat():dict(open=c+.1,high=c+1,low=c-1,close=c,volume=1000) for i,c in enumerate(prices)}
        rows={'SPY':bars,'XYZ':bars};base=replay(rows,'RSI2_SPY_REGIME','2020-07-01')
        extended=replay(rows,'RSI2_SPY_REGIME','2020-07-01',include_curve=True)
        self.assertEqual(base,{k:extended[k] for k in base})
        self.assertAlmostEqual(extended['curve'][-1]['equity'],extended['ending_marked_equity'])
        self.assertEqual(extended['entries'],extended['closed_trades']+len(extended['open_positions']))

    def test_intraday_study_does_not_count_untradeably_small_entries(self):
        from app.strategy_lab.broad_strategy_lab import replay
        f=features(pd.DataFrame(dict(date=pd.date_range('2020-01-01',periods=220,freq='5min',tz='UTC'),open=100.,high=101.,low=99.,close=99.,volume=100.)))
        r=replay({'BTC':f},Case('IBS_RANGE',.2),list(f['date']),fee=.001,slip=.0005,initial=10,min_notional=10)
        self.assertEqual(r['entries'],0);self.assertGreater(r['minimum_notional_exclusions'],0)

    def test_high_activity_losing_strategy_fails(self):
        r=dict(closed_trades=200,profit_factor=.9,maximum_drawdown=.1,activity=dict(entries_per_day=5,entry_active_day_fraction=.9,net_after_overhead=-100))
        self.assertFalse(gate(r))

    def test_profit_without_frequency_does_not_pass_daily_goal(self):
        r=dict(closed_trades=100,profit_factor=2,maximum_drawdown=.1,activity=dict(entries_per_day=.1,entry_active_day_fraction=.1,net_after_overhead=1000))
        self.assertFalse(gate(r))


if __name__=='__main__':unittest.main()

import math
import tempfile
from pathlib import Path
import unittest
import pandas as pd
from app.strategy_lab.broad_strategy_lab import Case,replay
from app.strategy_lab.regime_recovery_lab import grid,features,recovery_rules,aggregate_hourly,prepare,protocol


class RegimeRecoveryTests(unittest.TestCase):
    def frame(self,n=280):
        c=[100+8*math.sin(i/4)+i*.01 for i in range(n)]
        return pd.DataFrame(dict(date=pd.date_range('2023-01-01',periods=n,freq='h',tz='UTC'),
            open=c,close=c,high=[x+2 for x in c],low=[x-2 for x in c],volume=100.))

    def test_all_rules_are_causal_and_some_can_enter_without_long_ma_trend(self):
        f=self.frame();a=features(f);b=features(f.iloc[:250]);signals=0
        for case in grid():
            x=recovery_rules(a,case,'1h',.004,.0005);y=recovery_rules(b,case,'1h',.004,.0005)
            pd.testing.assert_series_equal(x['buy'].iloc[:250],y['buy'])
            pd.testing.assert_series_equal(x['sell'].iloc[:250],y['sell'])
            self.assertFalse(x['buy'].iloc[:200].any())
            signals+=int((x['buy']&(a['close']<a['sma200'])).sum())
        self.assertGreater(signals,0)
        self.assertEqual(len(grid()),96)
        self.assertEqual(len({c.identity for c in grid()}),96)

    def test_partial_hourly_groups_are_not_filled(self):
        f=self.frame(9).drop(index=2)
        result=aggregate_hourly(f)
        self.assertEqual(len(result),1)
        self.assertEqual(result.iloc[0]['date'],pd.Timestamp('2023-01-01T04:00Z'))

    def test_protocol_change_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'study';prepare(root)
            self.assertEqual(protocol(root)['selection_fee'],.004)
            (root/'protocol.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'FROZEN_PROTOCOL_CHANGED'):protocol(root)

    def simulate(self,*,floor=None,timeout=99,stop_low=99.,middle_close=100.5,last_open=99.):
        dates=list(pd.date_range('2023-01-01',periods=3,freq='h',tz='UTC'))
        bars={dates[0]:{'A':dict(open=100.,high=101.,low=99.,close=100.,volume=1.)},
              dates[1]:{'A':dict(open=100.,high=max(101.,middle_close),low=99.,close=middle_close,volume=1.)},
              dates[2]:{'A':dict(open=last_open,high=max(last_open,101.),low=min(stop_low,last_open),close=100.,volume=1.)}}
        cfg={'A':dict(stop_atr=1.,target_atr=None,trail_atr=None,timeout=timeout)}
        signals={'A':{d:(i==0,i==1,10.) for i,d in enumerate(dates)}}
        return replay({},Case('test',0),dates,fee=0.,slip=0.,initial=1000.,slots=1,
                      prepared=(bars,cfg,signals),signal_exit_floor=floor)

    def test_profit_floor_blocks_signal_exit_but_not_hard_stop(self):
        self.assertEqual(self.simulate()['closed_trades'],1)
        self.assertEqual(self.simulate(floor=.02)['closed_trades'],0)
        stopped=self.simulate(floor=.02,stop_low=85.)
        self.assertEqual(stopped['closed_trades'],1)
        self.assertEqual(stopped['trades'][0]['reason'],'INTRABAR_STOP')

    def test_time_exit_bypasses_floor_and_next_open_can_gap_below_floor(self):
        self.assertEqual(self.simulate(floor=.02,timeout=1)['closed_trades'],1)
        result=self.simulate(floor=.02,middle_close=105.,last_open=95.)
        self.assertEqual(result['closed_trades'],1)
        self.assertLess(result['realized_net'],0)

if __name__=='__main__':unittest.main()

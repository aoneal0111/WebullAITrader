import unittest
import pandas as pd
from app.strategy_lab.broad_strategy_lab import Case,indicators,replay,rules


def frames(rows):
    f=pd.DataFrame([dict(date=pd.Timestamp('2020-01-01',tz='UTC')+pd.Timedelta(days=i),open=o,high=h,low=l,close=c,volume=100.) for i,(o,h,l,c) in enumerate(rows)])
    return {'SPY':indicators(f)}


class BroadReplayTests(unittest.TestCase):
    def test_completed_signal_waits_for_next_open_and_open_is_not_a_win(self):
        f=frames([(100,101,99,99),(100,101,99,100),(100,101,99,100)])
        r=replay(f,Case('IBS_RANGE',.1),list(f['SPY']['date']),fee=0,slip=0,slots=1)
        # ATR needs history; an undefined ATR refuses the bracket rather than fabricating risk.
        self.assertEqual(r['entries'],0)
        f['SPY']['atr']=1.
        r=replay(f,Case('IBS_RANGE',.1),list(f['SPY']['date']),fee=0,slip=0,slots=1)
        self.assertEqual(r['entries'],1);self.assertEqual(r['events'][0]['at'],str(f['SPY']['date'].iloc[1]))
        self.assertEqual(r['closed_trades'],0);self.assertIsNone(r['win_rate']);self.assertEqual(r['open_positions'],1)

    def test_both_fees_are_included_and_cash_reconciles(self):
        f=frames([(100,101,99,99),(100,101,99,101),(102,103,101,102)]);f['SPY']['atr']=1.
        r=replay(f,Case('IBS_RANGE',.1),list(f['SPY']['date']),fee=.01,slip=0,slots=1,initial=1000)
        expected=(1000/(100*1.01))*102*.99-1000
        self.assertEqual(r['closed_trades'],1);self.assertEqual(r['wins'],0)
        self.assertAlmostEqual(r['realized_net'],expected,places=5)
        self.assertAlmostEqual(r['ending_cash'],1000+r['realized_net'],places=5)

    def test_gap_stop_uses_worse_open_not_a_favorable_stop_price(self):
        f=frames([(100,101,99,99),(100,101,99,100),(80,81,79,80)]);f['SPY']['atr']=1.
        r=replay(f,Case('IBS_RANGE',.1),list(f['SPY']['date']),fee=0,slip=0,slots=1)
        self.assertEqual(r['trades'][0]['reason'],'GAP_STOP');self.assertEqual(r['trades'][0]['exit_price'],80)

    def test_same_bar_stop_and_target_chooses_stop_first(self):
        f=frames([(100,101,99,100),(100,104,97,100)]);dates=list(f['SPY']['date'])
        cfg={'SPY':dict(timeout=10,stop_atr=2.,target_atr=3.,trail_atr=None)}
        bars={d:{'SPY':dict(open=o,high=h,low=l,close=c)} for d,(o,h,l,c) in zip(dates,[(100,101,99,100),(100,104,97,100)])}
        signals={'SPY':{dates[0]:(True,False,1.),dates[1]:(False,False,1.)}}
        r=replay(f,Case('TREND_PULLBACK',10),dates,fee=0,slip=0,slots=1,prepared=(bars,cfg,signals))
        self.assertEqual(r['trades'][0]['reason'],'INTRABAR_STOP');self.assertEqual(r['trades'][0]['exit_price'],98)

    def test_future_bars_cannot_change_past_indicators(self):
        a=frames([(100+i*.2,102+i*.2,98+i*.2,100+i*.2) for i in range(260)])['SPY']
        b=a.iloc[:240][['date','open','high','low','close','volume']].copy()
        b=indicators(b)
        pd.testing.assert_frame_equal(a.iloc[:240].reset_index(drop=True),b)

    def test_future_prices_cannot_rewrite_earlier_order_events(self):
        f=frames([(100,101,99,99),(100,101,99,101),(102,103,101,102),(103,104,102,103)]);f['SPY']['atr']=1.
        full=replay(f,Case('IBS_RANGE',.1),list(f['SPY']['date']),fee=0,slip=0,slots=1)
        short={'SPY':f['SPY'].iloc[:3].copy()};part=replay(short,Case('IBS_RANGE',.1),list(short['SPY']['date']),fee=0,slip=0,slots=1)
        self.assertEqual(full['events'][:len(part['events'])],part['events'])

    def test_invalid_prices_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'OHLC_SHAPE'):frames([(100,99,101,100)])

    def test_costs_cannot_be_negative(self):
        f=frames([(100,101,99,100)])
        with self.assertRaisesRegex(ValueError,'BOUNDED'):replay(f,Case('IBS_RANGE',.1),list(f['SPY']['date']),fee=-.1,slip=0)

    def test_new_symbol_eligibility_blocks_early_entries(self):
        f=frames([(100,101,99,99)]*220)['SPY']
        f['eligible']=f.index>=200
        signal=rules(f,Case('IBS_RANGE',.1))['buy']
        self.assertFalse(signal.iloc[:200].any())
        self.assertTrue(signal.iloc[200:].all())

    def test_eligibility_after_warmup_preserves_existing_replay(self):
        f=frames([(100,101,99,99),(100,101,99,101),(102,103,101,102)])
        f['SPY']['atr']=1.
        before=replay(f,Case('IBS_RANGE',.1),list(f['SPY']['date']),fee=.001,slip=.0005)
        f['SPY']['eligible']=True
        after=replay(f,Case('IBS_RANGE',.1),list(f['SPY']['date']),fee=.001,slip=.0005)
        self.assertEqual(before,after)


if __name__=='__main__':unittest.main()

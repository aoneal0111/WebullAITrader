"""Risk/causality and default-preservation checks for frozen adaptations."""
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
import unittest

from app.strategy_research.adaptations import Adaptation,VARIANTS,compare_adaptations
from app.strategy_research.candidates import Bar,Candidate
from app.strategy_research.replay import Costs,replay

START=datetime(2024,1,1,14,tzinfo=UTC)
ZERO=Costs(D(0),D(0))


def history(count=365):
    rows=[]
    for i in range(count):
        at=START+timedelta(days=i);p=D(100)+D(i)/10
        rows.append(Bar('TEST',at,at+timedelta(hours=6),p,p+D(1),p-D(1),p,D(1000),at+timedelta(hours=6)))
    rows[190]=replace(rows[190],low=D(80))
    rows[200]=replace(rows[200],open=D(119),low=D(118),high=D(125),close=D('118.5'))
    return tuple(rows)


class AdaptationTests(unittest.TestCase):
    def test_initial_stop_is_from_signal_history_and_never_widens_after_entry(self):
        bars=list(history());bars[202]=replace(bars[202],low=D(40))
        trades=replay(tuple(bars),Candidate.IBS_DAILY_REVERSION,ZERO,timeframe='daily',stop_lookback=20)
        self.assertTrue(trades)
        trade=trades[0]
        self.assertEqual(trade.entry_at,bars[201].opened.isoformat())
        self.assertEqual(trade.stop,D(80)*D('0.9995'))
        self.assertEqual(trade.exit_price,trade.stop)
        self.assertEqual(trade.reason,'STOP_FIRST')

    def test_short_reversion_timeout_keeps_precommitted_duration(self):
        bars=history();trades=replay(bars,Candidate.IBS_DAILY_REVERSION,ZERO,timeframe='daily',stop_lookback=10,max_hold=5)
        self.assertEqual(trades[0].reason,'TIME_EXIT')
        self.assertEqual(trades[0].exit_at,bars[205].closed.isoformat())
        self.assertEqual(trades[0].stop,D(118)*D('0.9995'))

    def test_default_replay_is_unchanged_and_minute_safety_scope_is_preserved(self):
        bars=history()
        self.assertEqual(replay(bars,Candidate.IBS_DAILY_REVERSION,ZERO,timeframe='daily'),
                         replay(bars,Candidate.IBS_DAILY_REVERSION,ZERO,timeframe='daily',stop_lookback=5))
        with self.assertRaises(ValueError):
            replay(bars,Candidate.RSI2_DAILY,ZERO,timeframe='daily',stop_lookback=20)
        for value in (True,0,4,51,D(10)):
            with self.assertRaises(ValueError):
                replay(bars,Candidate.IBS_DAILY_REVERSION,ZERO,timeframe='daily',stop_lookback=value)

    def test_comparison_reports_every_frozen_variant_without_execution_or_selection(self):
        bars=history();result=compare_adaptations(bars,Costs(D(10),D('.005')),split_at=bars[260].opened)
        self.assertEqual(result['variants_tested'],4)
        self.assertFalse(result['execution_authorized'])
        self.assertEqual(result['decision'],'RESEARCH_ONLY_NO_AUTOMATIC_SELECTION')
        self.assertEqual([v['name'] for v in result['results']],[v.name for v in VARIANTS])
        self.assertTrue(all('evaluation_boundary_marks' in r['later_dates'] for r in result['results']))
        with self.assertRaises(ValueError):compare_adaptations(bars,ZERO,split_at=bars[200].opened)

    def test_variant_bounds_and_daily_only_scope(self):
        for values in ((Candidate.RSI2_DAILY,5,10),(Candidate.IBS_DAILY_REVERSION,0,5),
                       (Candidate.DONCHIAN20_DAILY,20,61),(Candidate.IBS_DAILY_REVERSION,True,5)):
            with self.assertRaises(ValueError):Adaptation('BAD',*values,'invalid fixture')


if __name__=='__main__':unittest.main()

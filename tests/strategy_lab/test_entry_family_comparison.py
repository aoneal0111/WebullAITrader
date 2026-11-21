import unittest
import numpy as np
import pandas as pd
from app.strategy_lab.broad_strategy_lab import Case,indicators,rules
from app.strategy_lab.entry_family_comparison import GRID,prepared


class EntryFamilyComparisonTests(unittest.TestCase):
    def frame(self):
        x=np.arange(300);c=100+np.sin(x/8)*3+x*.02
        f=indicators(pd.DataFrame(dict(date=pd.date_range('2026-01-01',periods=300,freq='h',tz='UTC'),
            open=c-.1,high=c+.4,low=c-.4,close=c,volume=100)))
        f['eligible']=f.index>=200;return f

    def test_all_36_entry_cases_keep_the_same_exit_geometry_and_signals(self):
        f=self.frame();source=rules(f,Case('ROC3_PULLBACK',.08));count=0
        for family,values in GRID.items():
            for value in values:
                count+=1;case=Case(family,value);_,exits,signals=prepared({'X':f},case)
                self.assertEqual(exits['X']['stop_atr'],3)
                self.assertEqual(exits['X']['timeout'],10)
                self.assertIsNone(exits['X']['target_atr']);self.assertIsNone(exits['X']['trail_atr'])
                self.assertEqual([v[1] for v in signals['X'].values()],list(source['sell']))
                self.assertEqual([v[0] for v in signals['X'].values()],list(rules(f,case)['buy']))
        self.assertEqual(count,36)

    def test_entry_rules_cannot_see_future_candles(self):
        f=self.frame();prefix=f.iloc[:270]
        for family,values in GRID.items():
            for value in values:
                case=Case(family,value)
                self.assertEqual(list(rules(f,case)['buy'].iloc[:270]),list(rules(prefix,case)['buy']))


if __name__=='__main__':unittest.main()

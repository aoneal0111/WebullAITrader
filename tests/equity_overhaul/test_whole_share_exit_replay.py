from dataclasses import replace
from datetime import datetime,timedelta
from decimal import Decimal as D
import importlib.util
from pathlib import Path
import sys
import unittest

path=Path(__file__).resolve().parents[2]/'app/equity_overhaul/whole_share_exit_replay.py'
spec=importlib.util.spec_from_file_location('whole_share_under_test',path)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
START=datetime.fromisoformat('2026-07-01T19:57:00+00:00')
END=START+timedelta(minutes=3)


def path_for(values):
    return tuple(m.Candle(START+timedelta(minutes=i),START+timedelta(minutes=i+1),
        *(D(str(x)) for x in row)) for i,row in enumerate(values))


def reversal():
    return path_for([(10,11.2,9.5,11),(10.5,10.8,9.5,10),(9.5,10,9,9.5)])


def replay(bars=None,quantity=5,mode='HALF_1R_HALF_2R',costs=m.Costs()):
    return m.replay_exit_path(bars or reversal(),session_end=END,
        entry_trigger=D(10),initial_stop=D(9),quantity=quantity,mode=mode,costs=costs)


class WholeShareExitTests(unittest.TestCase):
    def test_odd_quantity_conserves_inventory_and_cash(self):
        r=replay();self.assertEqual([x['quantity'] for x in r['fills']],[5,2,3])
        self.assertEqual(r['net_pnl'],D(2))
        self.assertEqual(r['remaining_quantity'],0)
        self.assertFalse(r['execution_authorized'])

    def test_one_share_uses_full_target_without_fractional_exit(self):
        r=replay(quantity=1);self.assertTrue(r['one_share_partial_fallback'])
        self.assertFalse(r['partial_taken']);self.assertEqual(r['net_pnl'],D(-1))

    def test_minimum_commission_charged_on_every_actual_fill_order(self):
        r=replay(costs=m.Costs(minimum_commission_per_order=D(1)))
        self.assertEqual(r['commissions'],D(3));self.assertEqual(r['net_pnl'],D(-1))

    def test_partial_and_final_target_same_bar_are_two_sell_orders(self):
        bars=list(reversal());bars[0]=replace(bars[0],high=D(12),close=D(12))
        r=replay(bars,quantity=5,costs=m.Costs(minimum_commission_per_order=D('.5')))
        self.assertEqual(r['gross_pnl'],D(8));self.assertEqual(r['commissions'],D('1.5'))
        self.assertEqual([x['reason'] for x in r['fills'][1:]],['PARTIAL_1R','TARGET_2R'])

    def test_original_stop_wins_same_bar_target_ambiguity(self):
        bars=list(reversal());bars[0]=replace(bars[0],low=D('8.5'),high=D(12))
        r=replay(bars);self.assertEqual(r['net_pnl'],D(-5));self.assertFalse(r['partial_taken'])

    def test_partial_does_not_raise_stop_retroactively(self):
        bars=path_for([(10,11.2,9.5,11),(11,12,10.5,12),(12,12,11,12)])
        self.assertEqual(replay(bars)['net_pnl'],D(8))

    def test_gap_after_partial_can_make_whole_trade_lose(self):
        bars=list(reversal());bars[1]=replace(bars[1],open=D(8),high=D(9),low=D(8),close=D(9))
        r=replay(bars);self.assertEqual(r['net_pnl'],D(-4))
        self.assertEqual(r['fills'][-1]['reason'],'GAP_STOP')

    def test_unfilled_trigger_and_gap_below_initial_stop_have_no_fill(self):
        for replacement in (dict(high=D('9.8'),open=D('9.5'),close=D('9.5')),dict(open=D(8),low=D(8))):
            bars=list(reversal());bars[0]=replace(bars[0],**replacement)
            self.assertEqual(replay(bars)['status'],'NO_ENTRY')

    def test_missing_tail_and_internal_gap_refused_even_if_target_would_fill(self):
        bars=list(reversal());bars[0]=replace(bars[0],high=D(12))
        for subset in (bars[:-1],[bars[0],bars[2]]):
            with self.assertRaises(ValueError):replay(subset)

    def test_untouched_position_closes_at_session_end(self):
        bars=path_for([(10,10.2,9.5,10),(10,10.2,9.5,10),(10,10.5,9.5,10.5)])
        r=replay(bars);self.assertEqual(r['fills'][-1]['reason'],'SESSION_END')
        self.assertEqual(r['net_pnl'],D('2.5'))

    def test_cost_stress_reduces_identical_fixed_stop_path(self):
        base=replay(mode='FULL_2R')
        costed=replay(mode='FULL_2R',costs=m.Costs(adverse_bps_per_side=D(10),commission_per_share=D('.0035')))
        self.assertLess(costed['net_pnl'],base['net_pnl'])

    def test_paired_modes_share_entry_quantity_and_costs(self):
        r=m.compare_exit_paths(reversal(),session_end=END,entry_trigger=D(10),initial_stop=D(9),quantity=5)
        a,b=r['policies'];self.assertEqual(a['fills'][0],b['fills'][0])
        self.assertEqual((a['net_pnl'],b['net_pnl']),(D(-5),D(2)))
        self.assertEqual(r['promotion'],'NONE')

    def test_invalid_costs_quantities_and_prices_refused(self):
        for qty in (True,0,D('1.5'),1_000_001):
            with self.subTest(qty=qty),self.assertRaises(ValueError):replay(quantity=qty)
        for bps in (D('NaN'),D(-1),D(10000)):
            with self.subTest(bps=bps),self.assertRaises(ValueError):m.Costs(adverse_bps_per_side=bps)
        bars=list(reversal());bars[0]=replace(bars[0],close=D('NaN'))
        with self.assertRaises(ValueError):replay(bars)


if __name__=='__main__':unittest.main()

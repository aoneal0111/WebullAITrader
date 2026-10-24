from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from app.strategy_research.adaptations import VARIANTS
from app.strategy_research.candidates import Bar,Signal
from app.strategy_research.portfolio_replay import PortfolioLimits,portfolio_replay
from app.strategy_research.replay import Costs

ZERO=Costs(D(0),D(0))
LIMITS=PortfolioLimits(D(10000),D('.01'),D('.02'),D('.5'),D(1),2)


def dataset(symbol='AAA',count=6):
    start=datetime(2024,1,2,14,30,tzinfo=UTC)
    return tuple(Bar(symbol,start+timedelta(days=i),start+timedelta(days=i,hours=6),
        D(100),D(101),D(99),D(100),D(1000),start+timedelta(days=i,hours=6)) for i in range(count))


def signal_once(history,candidate):
    return Signal(D(90),'fixture') if len(history)==1 else None


def run(data,*,costs=ZERO,limits=LIMITS,end=None,exits=False):
    with patch('app.strategy_research.portfolio_replay.entry_signal',side_effect=signal_once), \
         patch('app.strategy_research.portfolio_replay.exit_signal',return_value=exits):
        return portfolio_replay(data,VARIANTS[0],costs,limits,end=end)


class PortfolioReplayTests(unittest.TestCase):
    def test_shared_risk_whole_shares_and_open_inventory_reconciliation(self):
        result=run({'BBB':dataset('BBB'),'AAA':dataset()})
        self.assertEqual([(p['symbol'],p['quantity']) for p in result['open_positions']],[('AAA',10),('BBB',10)])
        self.assertEqual(result['ending_cash'],D(8000))
        self.assertEqual(result['ending_liquidation_equity'],D(10000))
        self.assertEqual(result['closed_positions'],0)
        self.assertFalse(result['portfolio_verification_complete'])
        self.assertFalse(result['execution_authorized'])

    def test_capacity_rejection_is_deterministic_and_cannot_overdraw_cash(self):
        limits=replace(LIMITS,risk_fraction=D(1),total_risk_fraction=D(1),symbol_fraction=D(1),max_positions=1)
        result=run({'BBB':dataset('BBB'),'AAA':dataset()},limits=limits)
        self.assertEqual(result['open_positions'][0]['symbol'],'AAA')
        self.assertEqual(result['open_positions'][0]['quantity'],100)
        self.assertEqual(result['rejected_entries'][0]['reason'],'POSITION_LIMIT')
        self.assertTrue(all(p['cash']>=0 for p in result['equity_curve']))

    def test_open_equity_includes_both_execution_sides_and_fees(self):
        costs=Costs(D(10),D('.005'))
        result=run({'AAA':dataset()},costs=costs)
        q=result['open_positions'][0]['quantity']
        expected=LIMITS.initial_cash-q*(costs.buy(D(100))-costs.sell(D(100))+2*costs.fee_per_share)
        self.assertEqual(result['ending_liquidation_equity'],expected)
        self.assertLess(result['net_return'],0)
        self.assertEqual(result['realized_net_pnl'],0)
        self.assertEqual(result['open_position_net_pnl'],expected-LIMITS.initial_cash)

    def test_gap_stop_and_closed_equity_reconcile(self):
        bars=list(dataset());bars[2]=replace(bars[2],open=D(80),low=D(79),high=D(81),close=D(80))
        result=run({'AAA':tuple(bars)})
        self.assertEqual(result['trades'][0]['reason'],'GAP_STOP')
        self.assertEqual(result['trades'][0]['net_pnl'],D(-200))
        self.assertEqual(result['ending_liquidation_equity'],D(9800))
        self.assertEqual(result['realized_net_pnl'],D(-200))
        self.assertEqual(result['open_position_net_pnl'],0)
        self.assertEqual(result['sampled_equity_drawdown'],D('.02'))

    def test_exposure_budget_reserves_immediate_liquidation_costs(self):
        limits=replace(LIMITS,risk_fraction=D(1),total_risk_fraction=D(1),symbol_fraction=D(1))
        result=run({'AAA':dataset()},limits=limits,costs=Costs(D(100),D(1)))
        self.assertLessEqual(result['maximum_gross_to_equity'],1)
        self.assertGreaterEqual(result['ending_cash'],0)

    def test_opening_exit_releases_cash_but_close_exit_cannot_fund_same_open(self):
        a=list(dataset());a[2]=replace(a[2],open=D(80),low=D(79),high=D(81),close=D(80))
        limits=replace(LIMITS,max_positions=1)
        def signals(history,candidate):
            n=len(history);s=history[0].symbol
            return Signal(D(90),'fixture') if (s=='AAA' and n==1) or (s=='BBB' and n==2) else None
        with patch('app.strategy_research.portfolio_replay.entry_signal',side_effect=signals),patch('app.strategy_research.portfolio_replay.exit_signal',return_value=False):
            result=portfolio_replay({'AAA':tuple(a),'BBB':dataset('BBB')},VARIANTS[0],ZERO,limits)
        self.assertEqual(result['open_positions'][0]['symbol'],'BBB')
        a[2]=replace(a[2],open=D(100),high=D(101),low=D(80),close=D(100))
        with patch('app.strategy_research.portfolio_replay.entry_signal',side_effect=signals),patch('app.strategy_research.portfolio_replay.exit_signal',return_value=False):
            result=portfolio_replay({'AAA':tuple(a),'BBB':dataset('BBB')},VARIANTS[0],ZERO,limits)
        self.assertEqual(result['rejected_entries'][0]['reason'],'POSITION_LIMIT')
        self.assertFalse(result['open_positions'])

    def test_future_close_and_full_day_volume_do_not_change_open_sizing(self):
        a=dataset();b=list(a);b[1]=replace(b[1],high=D(1000),close=D(1000),volume=D(0))
        original=run({'AAA':a},end=2);changed=run({'AAA':tuple(b)},end=2)
        self.assertEqual(original['open_positions'],changed['open_positions'])
        self.assertEqual(original['ending_cash'],changed['ending_cash'])

    def test_single_symbol_closed_trade_mechanics_match_verified_screen(self):
        from app.strategy_research.replay import replay
        from tests.strategy_research.test_daily_reference import prices
        bars=tuple(replace(b,volume=D(1000)) for b in prices(1))
        for variant in VARIANTS:
            with self.subTest(variant=variant.name):
                actual=portfolio_replay({'TEST':bars},variant,ZERO,LIMITS)
                expected=replay(bars,variant.candidate,ZERO,timeframe='daily',
                    max_hold=variant.max_hold,stop_lookback=variant.stop_lookback)
                fields=('signal_at','entry_at','exit_at','entry_price','exit_price','stop','reason')
                self.assertEqual([tuple(t[k] for k in fields) for t in actual['trades']],
                    [tuple(getattr(t,k) for k in fields) for t in expected if t.reason!='EVALUATION_BOUNDARY'])

    def test_signal_exit_is_next_open_and_precommitted_timeout_is_at_close(self):
        result=run({'AAA':dataset()},exits=True)
        self.assertEqual(result['trades'][0]['reason'],'SIGNAL_EXIT')
        self.assertEqual(result['trades'][0]['exit_at'],dataset()[2].opened.isoformat())
        with patch('app.strategy_research.portfolio_replay.entry_signal',side_effect=signal_once), \
             patch('app.strategy_research.portfolio_replay.exit_signal',return_value=False):
            result=portfolio_replay({'AAA':dataset()},replace(VARIANTS[0],max_hold=2),ZERO,LIMITS)
        self.assertEqual(result['trades'][0]['reason'],'TIME_EXIT')
        self.assertEqual(result['trades'][0]['exit_at'],dataset()[2].closed.isoformat())

    def test_gap_entry_cancellation_and_same_bar_stop(self):
        bars=list(dataset());bars[1]=replace(bars[1],open=D(80),low=D(79),high=D(81),close=D(80))
        result=run({'AAA':tuple(bars)})
        self.assertEqual(result['rejected_entries'][0]['reason'],'GAP_THROUGH_STOP')
        self.assertEqual(result['ending_cash'],LIMITS.initial_cash)
        bars[1]=replace(bars[1],open=D(100),high=D(101),low=D(89),close=D(100))
        result=run({'AAA':tuple(bars)})
        self.assertEqual(result['trades'][0]['reason'],'STOP_FIRST')
        self.assertEqual(result['trades'][0]['net_pnl'],D(-100))

    def test_rejects_missing_dates_and_invalid_limits(self):
        with self.assertRaises(ValueError):run({'AAA':dataset(),'BBB':dataset('BBB')[:-1]})
        for change in ({'gross_fraction':D(2)},{'initial_cash':D('NaN')},{'max_positions':True}):
            with self.assertRaises(ValueError):replace(LIMITS,**change)

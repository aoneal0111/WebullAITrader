"""Differential verification with an independently structured daily simulator."""
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from random import Random
import unittest
from unittest.mock import patch

from app.strategy_research.adaptations import VARIANTS
from app.strategy_research.candidates import Bar
from app.strategy_research.replay import Costs, replay
from tests.strategy_research.daily_reference import reference_daily


def prices(seed):
    rng = Random(seed)
    rows = []
    price = D(100)
    for i in range(520):
        at = datetime(2022,1,1,14,tzinfo=UTC)+timedelta(days=i)
        opening = max(D(5),price+D(rng.randrange(-400,401))/100)
        close = max(D(5),opening+D(rng.randrange(-500,501))/100)
        high = max(opening,close)+D(rng.randrange(1,301))/100
        low = min(opening,close)-D(rng.randrange(1,301))/100
        rows.append(Bar('TEST',at,at+timedelta(hours=6),opening,high,low,close,
            D(0) if i%47 == 0 else D(1000),at+timedelta(hours=6)))
        price = close
    return tuple(rows)


class IndependentDailyTests(unittest.TestCase):
    def test_separate_trade_traversal_matches_every_field(self):
        reasons = set()
        count = 0
        for seed in range(6):
            bars = prices(seed)
            for v in VARIANTS:
                for start,end in ((0,360),(300,520)):
                    for bps,fee in ((D(0),D(0)),(D(10),D('.005')),(D(20),D('.01'))):
                        with self.subTest(seed=seed,variant=v.name,start=start,bps=bps):
                            actual = replay(bars,v.candidate,Costs(bps,fee),timeframe='daily',
                                start=start,end=end,stop_lookback=v.stop_lookback,max_hold=v.max_hold)
                            expected = reference_daily(bars,v.candidate.value,bps=bps,fee=fee,
                                start=start,end=end,stop_days=v.stop_lookback,hold_days=v.max_hold)
                            self.assertEqual([asdict(t) for t in actual],expected)
                            reasons.update(t.reason for t in actual)
                            count += len(actual)
        self.assertGreater(count,100)
        self.assertTrue({'STOP_FIRST','GAP_STOP','TIME_EXIT','SIGNAL_EXIT','EVALUATION_BOUNDARY'} <= reasons)

    def test_oracle_does_not_call_production_signal_or_execution_functions(self):
        bars=prices(0)
        with patch('app.strategy_research.replay.replay',side_effect=AssertionError), \
             patch('app.strategy_research.candidates.entry_signal',side_effect=AssertionError), \
             patch('app.strategy_research.candidates.exit_signal',side_effect=AssertionError), \
             patch.object(Costs,'buy',side_effect=AssertionError), \
             patch.object(Costs,'sell',side_effect=AssertionError):
            rows=reference_daily(bars,'IBS_DAILY_REVERSION',bps=D(10),fee=D('.005'),
                start=0,end=520,stop_days=5,hold_days=10)
        self.assertTrue(rows)

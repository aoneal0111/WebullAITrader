"""Synthetic mechanics checks; no performance assertion or provider requests."""
import contextlib
import csv
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.strategy_research.candidates import Bar, Candidate, Signal, entry_signal, validate_bars
from app.strategy_research.replay import Costs, Trade, compare, metrics, replay
from app.strategy_research.__main__ import main


START = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
ZERO_COST = Costs(D(0), D(0))


def session(start=START, count=30):
    end = start + timedelta(minutes=count)
    return tuple(Bar("TEST", start + timedelta(minutes=i), start + timedelta(minutes=i+1),
                     D(100), D(101), D(99), D(100), D(1000), end) for i in range(count))


def once(history, candidate):
    return Signal(D(99), "fixture") if len(history) == 22 else None


class ComparisonTests(unittest.TestCase):
    def test_failed_breakdown_uses_preexisting_level(self):
        bars = session()
        signal_bar = replace(bars[21], open=D("99.2"), low=D(98), close=D("99.5"))
        history = (*bars[:21], signal_bar)
        self.assertIsNotNone(entry_signal(history, Candidate.FAILED_BREAKDOWN_RECLAIM))
        self.assertIsNone(entry_signal(bars[:22], Candidate.FAILED_BREAKDOWN_RECLAIM))

    def test_daily_warmup_and_timeframe_separation(self):
        self.assertIsNone(entry_signal(session(), Candidate.RSI2_DAILY))
        with self.assertRaisesRegex(ValueError, "timeframe"):
            replay(session(), Candidate.RSI2_DAILY, ZERO_COST, timeframe="minute")

    def test_daily_candidates_trigger_on_completed_pullback(self):
        bars = []
        for i in range(203):
            opened = START + timedelta(days=i)
            price = D(100) + D(i) / 10 if i < 200 else D("119.9") - D(2) * (i-199)
            bars.append(Bar("TEST", opened, opened+timedelta(hours=6), price,
                            price+D(1), price-D(1), price, D(1000000), opened+timedelta(hours=6)))
        for candidate in (Candidate.RSI2_DAILY, Candidate.IMPROVED_R2_DAILY):
            self.assertIsNotNone(entry_signal(tuple(bars), candidate))
        # Being oversold below the long-term trend is not this hypothesis.
        bars[-1] = replace(bars[-1], open=D(90), low=D(89), high=D(91), close=D(90))
        self.assertIsNone(entry_signal(tuple(bars), Candidate.RSI2_DAILY))

    def test_vwap_pullback_and_band_reentry_are_distinct_signals(self):
        bars = session(count=30)
        closes = [D(100)]*15 + [D(120), D(118), D(116), D(114), D(112), D(110), D(108), D(110)]
        history = tuple(replace(bars[i], open=p, high=p+D("0.1"), low=p-D("0.1"),
                                close=p, volume=D(100000) if i<15 else D(100))
                        for i,p in enumerate(closes))
        self.assertIsNotNone(entry_signal(history, Candidate.RSI2_VWAP_PULLBACK))
        prices = [D(100)]*20 + [D(98), D(99)]
        history = tuple(replace(bars[i], open=p-D("0.1"), high=p+D("0.2"), low=p-D("0.2"), close=p)
                        for i,p in enumerate(prices))
        self.assertIsNotNone(entry_signal(history, Candidate.VWAP_BAND_REENTRY))

    def test_next_open_entry_stop_first_and_no_overlap(self):
        bars = list(session())
        # Signal on bar 21; next open at 100, stop99, target101. Both hit.
        with patch("app.strategy_research.replay.entry_signal", side_effect=once):
            trades = replay(tuple(bars), Candidate.BREAKOUT_CONTROL, ZERO_COST, timeframe="minute")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].entry_at, bars[22].opened.isoformat())
        self.assertEqual(trades[0].signal_at, bars[21].closed.isoformat())
        self.assertEqual(trades[0].net_r, -1)
        self.assertEqual(trades[0].reason, "STOP_FIRST")

    def test_signal_bar_cannot_fill(self):
        bars = list(session())
        bars[21] = replace(bars[21], high=D(500))
        bars[22] = replace(bars[22], open=D(100), low=D("99.5"), high=D("100.5"))
        with patch("app.strategy_research.replay.entry_signal", side_effect=once):
            t = replay(tuple(bars), Candidate.BREAKOUT_CONTROL, ZERO_COST, timeframe="minute")
        self.assertNotEqual(t[0].exit_at, bars[21].closed.isoformat())
        self.assertLessEqual(t[0].exit_price, 101)

    def test_gap_through_stop_cancels_entry(self):
        bars = list(session())
        bars[22] = replace(bars[22], open=D(98), low=D(97), close=D(98))
        with patch("app.strategy_research.replay.entry_signal", side_effect=once):
            self.assertEqual(replay(tuple(bars), Candidate.BREAKOUT_CONTROL, ZERO_COST, timeframe="minute"), ())

    def test_gap_stop_fills_at_worse_open(self):
        bars = list(session())
        bars[22] = replace(bars[22], low=D("99.5"), high=D("100.5"))
        bars[23] = replace(bars[23], open=D(97), low=D(96), close=D(98))
        with patch("app.strategy_research.replay.entry_signal", side_effect=once):
            t = replay(tuple(bars), Candidate.BREAKOUT_CONTROL, ZERO_COST, timeframe="minute")
        self.assertEqual(t[0].exit_price, 97)
        self.assertEqual(t[0].reason, "GAP_STOP")

    def test_costs_turn_small_gross_win_into_loss(self):
        bars = list(session())
        for i in range(22, 30):
            bars[i] = replace(bars[i], low=D("99.5"), high=D("100.1"), close=D("100.05"))
        with patch("app.strategy_research.replay.entry_signal", side_effect=once):
            a = replay(tuple(bars), Candidate.BREAKOUT_CONTROL, ZERO_COST, timeframe="minute")
            b = replay(tuple(bars), Candidate.BREAKOUT_CONTROL, Costs(D(10), D("0.01")), timeframe="minute")
        self.assertGreater(a[0].net_pnl_per_share, 0)
        self.assertLess(b[0].net_pnl_per_share, 0)

    def test_zero_volume_entry_is_not_filled(self):
        bars = list(session())
        bars[22] = replace(bars[22], volume=D(0))
        with patch("app.strategy_research.replay.entry_signal", side_effect=once):
            self.assertEqual(replay(tuple(bars), Candidate.BREAKOUT_CONTROL, ZERO_COST, timeframe="minute"), ())

    def test_input_validation_rejects_gaps_duplicates_nan_and_wrong_session(self):
        original = session()
        for bars in (original[:5]+original[6:], (original[0], *original),
                     (replace(original[0], close=D("NaN")), *original[1:]),
                     (replace(original[0], opened=START.replace(tzinfo=None)), *original[1:]),
                     original[:-1], original[1:]):
            with self.subTest(bars=bars[:1]), self.assertRaises(ValueError):
                validate_bars(bars, "minute")

    def test_prefix_causality(self):
        bars = session()
        signal = entry_signal(bars[:22], Candidate.FAILED_BREAKDOWN_RECLAIM)
        changed = (*bars[:22], *(replace(b, high=D(10000)) for b in bars[22:]))
        self.assertEqual(signal, entry_signal(changed[:22], Candidate.FAILED_BREAKDOWN_RECLAIM))
        with patch("app.strategy_research.replay.entry_signal", side_effect=once):
            a = replay(bars, Candidate.BREAKOUT_CONTROL, ZERO_COST, timeframe="minute", end=24)
            b = replay(changed, Candidate.BREAKOUT_CONTROL, ZERO_COST, timeframe="minute", end=24)
        # Both hit stop; future highs cannot rescue the trade.
        self.assertEqual(a, b)

    def test_split_discards_pre_split_pending_orders(self):
        bars = session() + session(START + timedelta(days=1))
        with patch("app.strategy_research.replay.entry_signal", side_effect=once):
            trades = replay(bars, Candidate.BREAKOUT_CONTROL, ZERO_COST,
                            timeframe="minute", start=30)
        self.assertTrue(trades)
        self.assertTrue(all(t.entry_at >= bars[30].opened.isoformat() for t in trades))
        self.assertTrue(all(t.signal_at > bars[30].opened.isoformat() for t in trades))

    def test_empty_and_breakeven_metrics(self):
        self.assertIsNone(metrics(())["win_rate"])
        def trade(net):
            return Trade("a", "b", "c", D(100), D(100)+net, D(99), net, net, "fixture")
        m = metrics((trade(D(1)), trade(D(0)), trade(D(-1))))
        self.assertAlmostEqual(m["win_rate"], 1/3)
        self.assertEqual(m["breakevens"], 1)
        self.assertEqual(m["closed_trade_drawdown_r"], 1)
        self.assertEqual(m["profit_factor_r"], 1)

    def test_truncated_file_mark_is_not_a_strategy_win(self):
        mark = Trade("a", "b", "c", D(100), D(110), D(99), D(10), D(10), "EVALUATION_BOUNDARY")
        result = metrics((mark,))
        self.assertEqual(result["closed_positions"], 0)
        self.assertIsNone(result["win_rate"])
        self.assertEqual(result["evaluation_boundary_marks"], 1)
        self.assertEqual(result["boundary_mark_net_r"], 10)

    def test_compare_no_authorization_and_all_variants_reported(self):
        bars = session() + session(START + timedelta(days=1))
        report = compare(bars, ZERO_COST, timeframe="minute", split_at=bars[30].opened)
        self.assertFalse(report["execution_authorized"])
        self.assertEqual(report["variants_tested"], 18)
        self.assertTrue(all("validation_double_cost" in r for r in report["results"]))
        with self.assertRaisesRegex(ValueError, "between sessions"):
            compare(bars, ZERO_COST, timeframe="minute", split_at=bars[10].opened)

    def test_cli_fingerprints_and_explicit_costs(self):
        bars = session() + session(START + timedelta(days=1))
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bars.csv"
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=asdict(bars[0]))
                writer.writeheader()
                writer.writerows(asdict(b) for b in bars)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main([str(path), "--timeframe", "minute", "--split-at", bars[30].opened.isoformat(),
                                       "--adverse-bps-per-side", "5", "--fee-per-share-per-side", "0.005"]), 0)
            report = json.loads(out.getvalue())
            self.assertEqual(len(report["input_sha256"]), 64)
            self.assertEqual(len(report["configuration_sha256"]), 64)
            self.assertEqual(len(report["implementation_sha256"]), 64)
            self.assertFalse(report["execution_authorized"])

    def test_bad_costs_fail(self):
        for adverse, fee in ((D(-1), D(0)), (D("NaN"), D(0)), (D(10000), D(0)), (D(0), D(-1))):
            with self.assertRaises(ValueError):
                Costs(adverse, fee)


class DailyLimitTests(unittest.TestCase):
    def bars(self):
        return tuple(replace(b, opened=START + timedelta(days=i),
            closed=START + timedelta(days=i, hours=6),
            session_end=START + timedelta(days=i, hours=6))
            for i, b in enumerate(session(count=8)))

    def run_limit(self, bars):
        def signal(history, candidate):
            return Signal(D(95), 'limit_fixture', D(98)) if len(history) == 1 else None
        with patch('app.strategy_research.replay.entry_signal', side_effect=signal):
            return replay(tuple(bars), Candidate.BOLLINGER_PERCENT_B_DAILY_LIMIT,
                          ZERO_COST, timeframe='daily', max_hold=2)

    def test_limit_must_penetrate_and_expires_after_one_session(self):
        bars = list(self.bars())
        bars[1] = replace(bars[1], low=D(98))
        bars[2] = replace(bars[2], low=D(97))
        self.assertEqual(self.run_limit(bars), ())

    def test_limit_gets_no_optimistic_gap_improvement(self):
        bars = list(self.bars())
        bars[1] = replace(bars[1], open=D(97), low=D(96))
        trades = self.run_limit(bars)
        self.assertEqual(trades[0].entry_price, D(98))

    def test_entry_bar_stop_wins_over_ambiguous_rebound(self):
        bars = list(self.bars())
        bars[1] = replace(bars[1], low=D(94))
        trades = self.run_limit(bars)
        self.assertEqual(trades[0].reason, 'STOP_FIRST')
        self.assertEqual(trades[0].net_r, -1)
        self.assertEqual(trades[0].entry_time_precision, 'BAR_WINDOW')

    def test_flat_bands_have_no_signal_and_exit_uses_completed_close(self):
        from app.strategy_research.candidates import percent_b, exit_signal
        self.assertIsNone(percent_b((D(100),) * 5))
        bars = list(self.bars())
        bars[-1] = replace(bars[-1], close=D(110), high=D(111))
        self.assertTrue(exit_signal(tuple(bars), Candidate.BOLLINGER_PERCENT_B_DAILY_LIMIT))


    def test_published_setup_requires_three_oversold_closes_trend_and_liquidity(self):
        prices = [D(100)] * 200 + list(map(D, [200, 200, 200, 200, 199, 195, 180]))
        template = self.bars()[0]
        bars = tuple(replace(template, close=p, open=p, high=p*D('1.05'),
                             low=p*D('0.95'), volume=D(200000)) for p in prices)
        candidate = Candidate.BOLLINGER_PERCENT_B_DAILY_LIMIT
        signal = entry_signal(bars, candidate)
        self.assertEqual(signal.entry_limit, D('176.4'))
        self.assertIsNone(entry_signal(bars[:-1], candidate))
        thin = (*bars[:-1], replace(bars[-1], volume=D(49999)))
        self.assertIsNone(entry_signal(thin, candidate))
        below_trend = tuple(replace(b, close=D(300)) for b in bars[:200]) + bars[200:]
        self.assertIsNone(entry_signal(below_trend, candidate))


if __name__ == "__main__":
    unittest.main()

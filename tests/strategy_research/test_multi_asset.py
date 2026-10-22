"""Causal mechanics and loss bounds, not strategy performance tests."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import unittest
import contextlib
import io
import json
import tempfile
from pathlib import Path
from app.strategy_research.candidates import Bar, Candidate, entry_signal
from app.strategy_research.multi_asset import Candle, PROFILES, PutLeg, PutCreditSpread, crypto_pullback, futures_pullback
from app.strategy_research.review_gate import ReviewEvidence, review

AT = datetime(2026, 10, 22, 16, tzinfo=UTC)


def candles(spacing, short=False):
    prices = [D(100+2*i) for i in range(25)]
    prices[-3:] = list(map(D, (144, 143, 147)))
    if short: prices = [D(200)-p for p in prices]
    return tuple(Candle(AT-spacing*(24-i), p+D('.25'), p-D('.25'), p) for i,p in enumerate(prices))


def leg(identity, strike, bid, ask):
    return PutLeg(identity, 'SPX', AT+timedelta(days=30), 'SPX_PM_20261121', D(strike), D(bid), D(ask), AT)


def spread():
    return PutCreditSpread(leg('short', '600', '2', '2.1'), leg('long', '595', '.8', '.9'), AT, D('.65'), D('.01'))


def evidence(**changes):
    return replace(ReviewEvidence(PROFILES[0].candidate, 200, 130, D('.2'), D('.1'), D('1.4'), D(4),
                                  3, True, True, True, True, True), **changes)


class MultiAssetTests(unittest.TestCase):
    def test_five_profiles_cannot_authorize(self):
        self.assertEqual(len({p.market for p in PROFILES}), 5)
        self.assertTrue(all(not p.execution_authorized for p in PROFILES))
        with self.assertRaises(ValueError): replace(PROFILES[0], execution_authorized=True)

    def test_crypto_long_pullback_needs_completed_higher_timeframe_trend(self):
        h, f = candles(timedelta(hours=1)), candles(timedelta(hours=4))
        result = crypto_pullback('BTC/USD', h, f, at=AT)
        self.assertEqual(result.side, 'LONG')
        self.assertLess(result.stop, result.reference)
        self.assertFalse(result.execution_authorized)
        self.assertIsNone(crypto_pullback('BTC/USD', h, candles(timedelta(hours=4), True), at=AT))
        for pair, hourly, higher in [('SOL/USD', h, f), ('BTC/USD', h[:10]+h[11:], f),
              ('BTC/USD', h, (*f[:-1], replace(f[-1], end=AT+timedelta(hours=4))))]:
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                crypto_pullback(pair, hourly, higher, at=AT)

    def test_futures_uses_actual_session_contract_ticks_and_both_directions(self):
        args = dict(at=AT, tick=D('.25'), multiplier=D(5), expires_at=AT+timedelta(days=50),
                    session_start=AT-timedelta(hours=3), session_end=AT+timedelta(hours=4))
        for short in (False, True):
            result = futures_pullback('MESZ26', candles(timedelta(minutes=5), short), **args)
            self.assertEqual(result.side, 'SHORT' if short else 'LONG')
            self.assertEqual(result.stop % D('.25'), 0)
        for symbol in ('MES', 'MES1!', 'BTCUSD'):
            with self.assertRaises(ValueError): futures_pullback(symbol, candles(timedelta(minutes=5)), **args)
        self.assertIsNone(futures_pullback('MESZ26', candles(timedelta(minutes=5)), **{**args, 'session_end': AT+timedelta(minutes=15)}))
        with self.assertRaises(ValueError):
            futures_pullback('MESZ26', candles(timedelta(minutes=5)), **{**args, 'session_start': AT-timedelta(hours=1)})

    def test_setup_requires_structural_stop_and_no_execution_authority(self):
        result = crypto_pullback('ETH/USD', candles(timedelta(hours=1)), candles(timedelta(hours=4)), at=AT)
        for changes in ({'execution_authorized': True}, {'stop': result.reference}, {'side': 'BUY'}):
            with self.assertRaises(ValueError): replace(result, **changes)

    def test_credit_spread_terminal_payoffs_include_losses_and_costs(self):
        s = spread()
        self.assertEqual(s.credit, D('1.08'))
        self.assertEqual(s.maximum_profit, D('103.40'))
        self.assertEqual(s.maximum_loss, D('396.60'))
        self.assertEqual(s.terminal_pnl(D(610)), s.maximum_profit)
        self.assertEqual(s.terminal_pnl(D(590)), -s.maximum_loss)
        self.assertEqual(s.terminal_pnl(D(598)), D('-96.60'))
        self.assertFalse(s.execution_authorized)

    def test_spread_rejects_missing_hedge_incompatible_settlement_stale_or_bad_credit(self):
        s = spread()
        for changes in ({'long': replace(s.long, expiry=AT+timedelta(days=31))},
                        {'long': replace(s.long, settlement_id='different_SOQ')},
                        {'long': replace(s.long, strike=D(605))},
                        {'long': replace(s.long, quoted_at=AT-timedelta(seconds=3))},
                        {'long': replace(s.long, bid=D(4), ask=D(5))}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(s, **changes)
        with self.assertRaises(ValueError): replace(s.short, exercise='AMERICAN')

    def test_spread_close_uses_buyback_ask_and_sale_bid(self):
        s = spread(); at = AT+timedelta(minutes=1)
        short = replace(s.short, bid=D(1), ask=D('1.1'), quoted_at=at)
        long = replace(s.long, bid=D('.3'), ask=D('.4'), quoted_at=at)
        self.assertEqual(s.closing_pnl(short, long, at=at), D('23.40'))
        with self.assertRaises(ValueError): s.closing_pnl(replace(short, quoted_at=at+timedelta(seconds=1)), long, at=at)
        with self.assertRaises(ValueError): s.closing_pnl(short, long, at=at+timedelta(seconds=3))

    def test_high_win_rate_with_negative_profit_is_rejected(self):
        result = review(evidence(wins=190, expectancy_r=D('-.2')))
        self.assertIn('NONPOSITIVE_NET_OR_STRESS_EXPECTANCY', result['reasons'])
        self.assertEqual(result['decision'], 'INSUFFICIENT_EVIDENCE')
        self.assertFalse(result['execution_authorized'])

    def test_small_sample_and_missing_execution_evidence_are_rejected(self):
        result = review(evidence(closed=19, wins=11, canonical_replay_passed=False))
        self.assertIn('FEWER_THAN_100_CLOSED_TRADES', result['reasons'])
        self.assertIn('CANONICAL_REPLAY_PASSED_MISSING', result['reasons'])
        self.assertEqual(review(evidence())['decision'], 'ELIGIBLE_FOR_MANUAL_REVIEW')
        self.assertFalse(review(evidence())['execution_authorized'])
        with self.assertRaises(ValueError): evidence(expectancy_r=D('NaN'))

    def test_opening_range_retest_uses_completed_opening_range(self):
        start = datetime(2026, 10, 22, 13, 30, tzinfo=UTC); end = start+timedelta(hours=6, minutes=30)
        prices = tuple(map(D, (100, 100, 100, 100, 100, 102, 101, 103)))
        bars = tuple(Bar('TEST', start+timedelta(minutes=i), start+timedelta(minutes=i+1),
                         p-D('.1'), p+D('.2'), p-D('.2'), p, D(100), end) for i,p in enumerate(prices))
        bars = (*bars[:6], replace(bars[6], low=D('100.1')), bars[7])
        self.assertIsNotNone(entry_signal(bars, Candidate.OPENING_RANGE_RETEST))
        self.assertIsNone(entry_signal(bars[:-1], Candidate.OPENING_RANGE_RETEST))
        with self.assertRaises(ValueError):
            entry_signal((replace(bars[0], session_end=end+timedelta(days=1)), *bars[1:]), Candidate.OPENING_RANGE_RETEST)

    def test_vwap_reclaim_requires_rising_trend(self):
        start = datetime(2026, 10, 22, 13, 30, tzinfo=UTC); end=start+timedelta(hours=6, minutes=30)
        prices = [D(100)+D(i)/10 for i in range(22)]; prices[-2:] = [D(101), D('102.5')]
        bars = tuple(Bar('TEST', start+timedelta(minutes=i), start+timedelta(minutes=i+1),
            p-D('.05'), p+D('.1'), p-D('.1'), p, D(100), end) for i,p in enumerate(prices))
        bars = (*bars[:-2], replace(bars[-2], low=D(100)), bars[-1])
        self.assertIsNotNone(entry_signal(bars, Candidate.TREND_VWAP_PULLBACK))
        flat = tuple(replace(b, open=D(100), high=D(101), low=D(99), close=D(100)) for b in bars)
        self.assertIsNone(entry_signal(flat, Candidate.TREND_VWAP_PULLBACK))

    def test_plan_cli_keeps_every_market_non_executable(self):
        from app.strategy_research.plan import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(len(result['profiles']), 5)
        self.assertFalse(result['execution_authorized'])
        self.assertIn('MULTI_LEG_EXECUTION_UNSUPPORTED', result['readiness']['OPTIONS'])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'evidence.json'
            path.write_text('{"expectancy_r": 1.2}')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                main(['--evidence', str(path)])
            self.assertEqual(exc.exception.code, 2)

    def test_terminal_bounds_do_not_cap_wide_quote_liquidation_loss(self):
        s = spread();at = AT+timedelta(minutes=1)
        short = replace(s.short, bid=D(8), ask=D(9), quoted_at=at)
        long = replace(s.long, bid=D(1), ask=D(2), quoted_at=at)
        self.assertLess(s.closing_pnl(short, long, at=at), -s.maximum_loss)
        self.assertEqual(s.terminal_two_outcome_breakeven, D('.7932'))


if __name__ == '__main__': unittest.main()

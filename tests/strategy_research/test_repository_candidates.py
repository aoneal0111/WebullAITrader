"""Causal strategy mechanics, not a claim that a public win rate transfers."""
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal as D
import unittest
from unittest.mock import patch

from app.strategy_research.candidates import Bar,Candidate,entry_signal,exit_signal,DAILY
from app.strategy_research.multi_asset import Candle
from app.strategy_research.repository_candidates import crypto_donchian,crypto_donchian_exit,crypto_ifr2_cloud,crypto_ifr2_exit,_hilbert_mode

END=datetime(2026,10,22,18,tzinfo=UTC)


def days():
    rows=[]
    for i in range(205):
        at=END-timedelta(days=205-i);price=D(100)+D(i)/10
        rows.append(Bar('TEST',at,at+timedelta(hours=6),price,price+D(1),price-D(1),price,D(1000),at+timedelta(hours=6)))
    return tuple(rows)


def six_hours():
    return tuple(Candle(END-timedelta(hours=6*(199-i)),D(100)+i+D('.1'),D(100)+i-D('.1'),D(100)+i) for i in range(200))


class RepositoryCandidateTests(unittest.TestCase):
    def test_ibs_entry_is_range_normalized_and_requires_trend_and_warmup(self):
        history=days();last=replace(history[-1],open=D(119),low=D(118),close=D('118.5'),high=D(125))
        hit=entry_signal((*history[:-1],last),Candidate.IBS_DAILY_REVERSION)
        self.assertIsNotNone(hit);self.assertLess(hit.stop,last.close)
        self.assertIsNone(entry_signal((*history[:100],last),Candidate.IBS_DAILY_REVERSION))
        for b in (replace(last,open=D(90),low=D(89),close=D('89.1'),high=D(95)),
                  replace(last,open=D(120),low=D(120),close=D(120),high=D(120))):
            self.assertIsNone(entry_signal((*history[:-1],b),Candidate.IBS_DAILY_REVERSION))

    def test_daily_donchian_excludes_signal_bar_and_exits_on_prior_channel(self):
        history=days();last=replace(history[-1],open=D(125),low=D(124),close=D(125),high=D(130))
        self.assertIsNotNone(entry_signal((*history[:-1],last),Candidate.DONCHIAN20_DAILY))
        self.assertIsNone(entry_signal(history,Candidate.DONCHIAN20_DAILY))
        low=replace(last,open=D(90),low=D(89),close=D(90),high=D(95))
        self.assertTrue(exit_signal((*history[:-1],low),Candidate.DONCHIAN20_DAILY))
        self.assertFalse(exit_signal((*history[:-1],last),Candidate.DONCHIAN20_DAILY))
        self.assertTrue(exit_signal((*history[:-1],last),Candidate.IBS_DAILY_REVERSION))
        self.assertIn(Candidate.DONCHIAN20_DAILY,DAILY)

    def test_crypto_donchian_has_structural_stop_and_no_execution_authority(self):
        bars=six_hours();hit=crypto_donchian('BTC/USD',bars,at=END)
        self.assertEqual(hit.side,'LONG');self.assertFalse(hit.execution_authorized)
        self.assertLess(hit.stop,hit.reference)
        self.assertIsNone(crypto_donchian('BTC/USD',bars[-199:],at=END))
        down=(*bars[:-1],replace(bars[-1],high=D(105),low=D(100),close=D(101)))
        self.assertIsNone(crypto_donchian('BTC/USD',down,at=END))
        self.assertTrue(crypto_donchian_exit(down,at=END))
        self.assertFalse(crypto_donchian_exit(bars,at=END))

    def test_crypto_rejects_future_stale_gapped_wrong_interval_and_other_assets(self):
        bars=six_hours()
        for values,at in ((bars,END-timedelta(seconds=1)),(bars,END+timedelta(hours=6)),
                          (bars[:50]+bars[51:],END),
                          (tuple(replace(b,end=END-timedelta(hours=199-i)) for i,b in enumerate(bars)),END)):
            with self.assertRaises(ValueError):crypto_donchian('BTC/USD',values,at=at)
        with self.assertRaises(ValueError):crypto_donchian('ADA/USD',bars,at=END)

    def test_future_bars_cannot_change_a_completed_prefix_signal(self):
        history=days();last=replace(history[-1],open=D(125),low=D(124),close=D(125),high=D(130))
        prefix=(*history[:-1],last);hit=entry_signal(prefix,Candidate.DONCHIAN20_DAILY)
        future=replace(last,opened=last.opened+timedelta(days=1),closed=last.closed+timedelta(days=1),session_end=last.session_end+timedelta(days=1),high=D(10000))
        self.assertEqual(hit,entry_signal((*prefix,future)[:-1],Candidate.DONCHIAN20_DAILY))


    def test_ifr2_never_omits_hilbert_filter_or_structural_stop(self):
        bars=list(six_hours())
        bars=[replace(b,end=END-timedelta(hours=199-i)) for i,b in enumerate(bars)]
        for i in range(195,200):
            price=D(280)-D(i-195)*2
            bars[i]=replace(bars[i],high=price+D('.1'),low=price-D('.1'),close=price)
        bars=tuple(bars)
        with patch('app.strategy_research.repository_candidates._hilbert_mode',return_value=1):
            hit=crypto_ifr2_cloud('BTC/USD',bars,at=END)
            self.assertIsNotNone(hit);self.assertFalse(hit.execution_authorized)
            self.assertLess(hit.stop,hit.reference)
        with patch('app.strategy_research.repository_candidates._hilbert_mode',return_value=0):
            self.assertIsNone(crypto_ifr2_cloud('BTC/USD',bars,at=END))
        with patch('app.strategy_research.repository_candidates._hilbert_mode',side_effect=RuntimeError('TA_LIB_RESEARCH_BACKEND_REQUIRED')):
            with self.assertRaises(RuntimeError):crypto_ifr2_cloud('BTC/USD',bars,at=END)
        raised=(*bars[:-1],replace(bars[-1],high=D(310),low=D(300),close=D(305)))
        self.assertTrue(crypto_ifr2_exit(raised,at=END))
        with self.assertRaises(ValueError):crypto_ifr2_cloud('BTC/USD',bars,at=END-timedelta(seconds=1))

    def test_hilbert_backend_does_not_change_global_indicator_settings(self):
        try:
            import talib
        except ImportError:
            self.skipTest('optional TA-Lib research extra unavailable')
        self.assertEqual(talib.__version__,'0.6.8')
        before=talib.get_unstable_period('HT_TRENDMODE')
        value=_hilbert_mode(tuple(D(100)+i for i in range(200)))
        self.assertIn(value,(0,1));self.assertEqual(talib.get_unstable_period('HT_TRENDMODE'),before)
        with patch.object(talib,'get_unstable_period',return_value=1):
            with self.assertRaises(RuntimeError):_hilbert_mode(tuple(D(100)+i for i in range(200)))


if __name__=='__main__':unittest.main()

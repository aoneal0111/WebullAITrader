"""Independent rule implementations and provenance for public-repo research.

No third-party bot is imported or executed. Repository reports are author claims,
not reproduced Atlas results. No production policy or activation path is changed.
"""
from datetime import timedelta
from decimal import Decimal as D
import json

from .multi_asset import ResearchSetup, _history
from app.indicators.rsi import rsi

REPOSITORIES = (
    dict(repository='QuantConnect/Lean',revision='f3d841e60dfd010ee5fc293ea893e56952484108',
         stars_at_review=22158,license='Apache-2.0',
         path='Algorithm.Python/Alphas/GlobalEquityMeanReversionIBSAlpha.py',
         finding='Daily cross-sectional IBS ranking; zero fees in example; no verified transferable win rate.'),
    dict(repository='jesse-ai/example-strategies',revision='7c91e0a37bf62165790120d730442e4f6eb00364',
         stars_at_review=181,license='MIT',path='Donchian/BTC-USDT_6h_results.txt',
         finding='Author-reported 18 closed BTC/USDT 6h trades in 2019-2020; 56% profitable, 46.89% drawdown.'),
    dict(repository='freqtrade/freqtrade-strategies',revision='f3340ce11f5bdf62f598522e64d1f5638eaa13f5',
         stars_at_review=5585,license='GPL-3.0',path='user_data/strategies/berlinguyinca/CombinedBinHAndCluc.py',
         finding='Five-minute crypto Bollinger/mean-reversion example; no reproduced result or Atlas equity equivalence.'),
    dict(repository='iterativv/NostalgiaForInfinity',revision='0cb81dcbfdb7a60f97cb55ebebbf1c8b0381fabf',
         stars_at_review=3491,license='GPL-3.0',path='tests/backtests/test_winrate_and_drawdown_year.py',
         finding='90% fixture target is not an observed result; missed win-rate/drawdown thresholds print notes.'),
    dict(repository='kernc/backtesting.py',revision='ca2e2611621e472542ba90f7243a1fa06a7d7108',
         stars_at_review=9111,license='AGPL-3.0',path='README.md',
         finding='Backtest engine; tutorial/example output does not establish an executable Atlas strategy.'),
)

ADAPTATIONS = (
    dict(candidate='IBS_DAILY_REVERSION',timeframe='EQUITY_DAILY',
         rules='Close above SMA200, IBS below 0.2; next-open entry, next-open exit after close exceeds prior high.',
         differences='Single-symbol long-only threshold, trend gate, structural stop and caller max hold; not Lean long/short rank portfolio.'),
    dict(candidate='DONCHIAN20_DAILY',timeframe='EQUITY_DAILY',
         rules='Close above SMA200 and prior 20 highs; exit after close below prior 20 lows.',
         differences='Daily equities rather than 6h BTC; structural stop, next-open fills, caller max hold and explicit costs.'),
    dict(candidate='IFR2_CLOUD_CRYPTO_RESEARCH',timeframe='BTC_ETH_SPOT_1H',
         rules='RSI2 below 10, close above displaced 20/30/120 cloud, TA-Lib HT_TRENDMODE=1; exit above prior two highs.',
         differences='Independent causal cloud, explicit TA-Lib 0.6.8 backend, BTC/ETH universe, structural stop; original engine parity and reported results not reproduced.'),
    dict(candidate='DONCHIAN20_CRYPTO_6H',timeframe='BTC_ETH_SPOT_6H',
         rules='Completed contiguous 6h bars; SMA200 and prior 20 highs entry; prior 20 lows exit.',
         differences='Signal-only; BTC/USD or ETH/USD, five-bar structural stop; no full-balance sizing or executable fill claim.'),
)


def crypto_donchian(symbol, bars, *, at):
    """Six-hour spot-only hypothesis. A partial hour is never a six-hour bar."""
    if symbol not in {'BTC/USD','ETH/USD'}:
        raise ValueError('Frozen BTC/USD and ETH/USD research universe required')
    _history(bars,at,timedelta(hours=6))
    if len(bars) < 200:
        return None
    current=bars[-1]
    average=sum((b.close for b in bars[-200:]),D(0))/200
    if current.close <= average or current.close <= max(b.high for b in bars[-21:-1]):
        return None
    stop=min(b.low for b in bars[-5:])*D('0.9995')
    return ResearchSetup('DONCHIAN20_CRYPTO_6H',symbol,'LONG',current.end,current.close,stop)


def crypto_donchian_exit(bars, *, at):
    _history(bars,at,timedelta(hours=6))
    return bars[-1].close < min(b.low for b in bars[-21:-1])


def _hilbert_mode(closes):
    # Optional research extra only. Never replace an unavailable filter with True.
    try:
        import numpy as np
        import talib
    except ImportError as exc:
        raise RuntimeError('TA_LIB_RESEARCH_BACKEND_REQUIRED') from exc
    if talib.__version__ != '0.6.8' or talib.get_unstable_period('HT_TRENDMODE') != 0:
        raise RuntimeError('TA_LIB_RESEARCH_BACKEND_MISMATCH')
    values=np.array([float(x) for x in closes],dtype=np.float64)
    if not np.isfinite(values).all():
        raise ValueError('FINITE_FLOAT_RESEARCH_INPUT_REQUIRED')
    return int(talib.HT_TRENDMODE(values)[-1])


def crypto_ifr2_cloud(symbol, bars, *, at):
    """IFR2-inspired hourly research; not parity with the author's old engine."""
    if symbol not in {'BTC/USD','ETH/USD'}:
        raise ValueError('Frozen BTC/USD and ETH/USD research universe required')
    _history(bars,at,timedelta(hours=1))
    if len(bars) < 200:
        return None
    closes=tuple(b.close for b in bars)
    # Cloud at t uses the span projected from t-60; no future data or backshift.
    projected=bars[:-60]
    def midpoint(n):
        window=projected[-n:]
        return (max(b.high for b in window)+min(b.low for b in window))/2
    span_a=(midpoint(20)+midpoint(30))/2
    span_b=midpoint(120)
    if not (rsi(closes,2)[-1] < 10 and bars[-1].close > max(span_a,span_b)):
        return None
    if _hilbert_mode(closes) != 1:
        return None
    stop=min(b.low for b in bars[-5:])*D('0.9995')
    return ResearchSetup('IFR2_CLOUD_CRYPTO_RESEARCH',symbol,'LONG',bars[-1].end,bars[-1].close,stop)


def crypto_ifr2_exit(bars, *, at):
    _history(bars,at,timedelta(hours=1))
    return bars[-1].close > max(b.high for b in bars[-3:-1])


def main():
    print(json.dumps(dict(scope='PUBLIC_REPOSITORY_RESEARCH_ONLY',execution_authorized=False,
        reviewed_on='2026-10-22',repositories=REPOSITORIES,adaptations=ADAPTATIONS,
        deferred=['IFR2 original reproduction: historical engine/cloud/Hilbert parity and venue data still required.',
                  'NFI and CombinedBinHAndCluc: no source code imported; independent reproduction required.']),indent=2))


if __name__=='__main__':main()

# Public repository strategy review

Reviewed 2026-10-22. Repository popularity is a discovery aid, not performance
validation. Pin the source revision before comparing code or results. No external
bot was installed, executed or connected to Atlas. Implementations below use
independent code for mathematical rules; GPL/AGPL strategy source was not copied.

| Project | Stars at review | Pinned revision | Finding |
|---|---:|---|---|
| QuantConnect/Lean | 22,158 | f3d841e60dfd010ee5fc293ea893e56952484108 | Daily ETF IBS example ranks both sides of a fixed universe and sets fees to zero. No transferable high-win result established. |
| freqtrade/freqtrade-strategies | 5,585 | f3340ce11f5bdf62f598522e64d1f5638eaa13f5 | Crypto examples require own exchange/pair/timeframe backtests; no Atlas result established. |
| iterativv/NostalgiaForInfinity | 3,491 | 0cb81dcbfdb7a60f97cb55ebebbf1c8b0381fabf | A 90% fixture target is not a measured win rate. The inspected test logs threshold misses instead of asserting failure. |
| kernc/backtesting.py | 9,111 | ca2e2611621e472542ba90f7243a1fa06a7d7108 | Backtest framework, not a verified profitable production strategy. |
| jesse-ai/example-strategies | 181 | 7c91e0a37bf62165790120d730442e4f6eb00364 | Inspect complete performance reports alongside strategy rules; profitable percentages alone can mislead. |

Primary files inspected (links freeze revisions):

- [Lean IBS alpha](https://github.com/QuantConnect/Lean/blob/f3d841e60dfd010ee5fc293ea893e56952484108/Algorithm.Python/Alphas/GlobalEquityMeanReversionIBSAlpha.py)
- [Freqtrade CombinedBinHAndCluc](https://github.com/freqtrade/freqtrade-strategies/blob/f3340ce11f5bdf62f598522e64d1f5638eaa13f5/user_data/strategies/berlinguyinca/CombinedBinHAndCluc.py)
- [NFI threshold test](https://github.com/iterativv/NostalgiaForInfinity/blob/0cb81dcbfdb7a60f97cb55ebebbf1c8b0381fabf/tests/backtests/test_winrate_and_drawdown_year.py)
- [NFI backtest instructions](https://github.com/iterativv/NostalgiaForInfinity/blob/0cb81dcbfdb7a60f97cb55ebebbf1c8b0381fabf/docs/backtesting.md)
- [Backtesting.py](https://github.com/kernc/backtesting.py/tree/ca2e2611621e472542ba90f7243a1fa06a7d7108)
- [Jesse Donchian rules](https://github.com/jesse-ai/example-strategies/blob/7c91e0a37bf62165790120d730442e4f6eb00364/Donchian/__init__.py)
- [Jesse BTC 6h report](https://github.com/jesse-ai/example-strategies/blob/7c91e0a37bf62165790120d730442e4f6eb00364/Donchian/BTC-USDT_6h_results.txt)
- [Jesse RSI2 report](https://github.com/jesse-ai/example-strategies/blob/7c91e0a37bf62165790120d730442e4f6eb00364/RSI2/RSI2-results.txt)
- [Jesse IFR2 rules](https://github.com/jesse-ai/example-strategies/blob/7c91e0a37bf62165790120d730442e4f6eb00364/IFR2/__init__.py)
- [Jesse IFR2 ADA report](https://github.com/jesse-ai/example-strategies/blob/7c91e0a37bf62165790120d730442e4f6eb00364/IFR2/ADA-USDT_1h_results.txt)

The author-provided Donchian BTC/USDT six-hour report covers 2019-2020: 18 closed
trades, 56% profitable, +451.94% return, 46.89% drawdown, versus +681.2% market
change. This is neither high-win proof nor an intraday scalper result.
The separate RSI2 BTC three-hour report has 116 trades, 63% profitable, but a
43.3% loss and 50.11% drawdown. IFR2's ADA hourly report has 260 trades, 69%
profitable, +141.23% and 26.61% drawdown over 2019-01-10 through 2021-04-01;
market change was +2218.44%. These are repository-author reports, not reproduced
Atlas results or evidence of current outperformance. Data, old engine versions,
portfolio exposure and execution assumptions require independent reproduction.

## Implemented research candidates

- `IBS_DAILY_REVERSION`: long-only single-symbol equity adaptation. Close above
  SMA200 and `(close-low)/(high-low)<0.2`; zero-range bars do not signal. Next-open
  entry; signal an exit when close exceeds the previous day's high, filled at
  the next open. This differs from Lean's cross-sectional long/short ranks,
  universe, sizing and one-day prediction horizon. It is a new hypothesis.
- `DONCHIAN20_DAILY`: long-only equity adaptation. Close above SMA200 and the
  prior twenty highs, excluding the current candle. Exit after close falls below
  the prior twenty lows. Daily equity bars replace the source's six-hour crypto.
- `DONCHIAN20_CRYPTO_6H`: BTC/USD or ETH/USD spot research signal with completed,
  aligned, contiguous six-hour candles, SMA200 and the same prior-channel rules.
  At least 200 bars are required; hourly candles cannot be substituted for six
  hours. There is no full-balance sizing, order creation or fill assumption.
- `IFR2_CLOUD_CRYPTO_RESEARCH`: independent hourly BTC/ETH hypothesis using RSI2
  below ten, a 20/30/120 cloud projected from sixty completed bars earlier and
  `HT_TRENDMODE=1`. Exit above the prior two highs. A missing/mismatched Hilbert
  backend fails closed; no substitute filter is silently used. This is not an
  exact recreation of the old author's engine, cloud or indicator startup.

All retain structural stops. The equity replay retains caller-selected holding
limits (default ten bars), modeled costs, one-unit sizing, next-open fills and
boundary-mark exclusions. These changes prevent attaching source returns or win
rates to Atlas adaptations. Daily candidates enter the existing daily comparison;
minute strategies remain separate. NFI and the GPL crypto composite are deferred
pending independent reproduction; neither was installed into runtime.

The optional `repository-research` package extra pins TA-Lib 0.6.8 for the IFR2
research filter. It is not a normal app dependency. Its test checks the actual
backend when the extra is present; base installations skip only that optional
backend check. Indicator settings are checked, never changed. No GUI activation,
policy selector, leverage, live-account access, or hard safety control is changed.
Run `python -m app.strategy_research.repository_candidates` for the source catalog.

## Validation scope

Mechanics tests check trend/warmup conditions, zero-range IBS, excluding the signal
bar from Donchian levels, causal prefixes, future/stale/gapped/wrong-interval crypto
rejection, Hilbert fail-closed behavior and non-executable structural-risk results.
Historical performance comparisons remain private research artifacts. Existing
visible dates are exploratory; independently measured costs, new date windows,
untouched holdouts and PAPER execution evidence remain required for review.

# Atlas strategy research: 7 November 2026

This research checked 297 repository candidates: 264 README contents were retrieved and screened; 33 were unavailable. These are content screens, not 264 code audits. A separate pinned inspection parsed 68 Python strategy files from `freqtrade/freqtrade-strategies` at `f3340ce11f5bdf62f598522e64d1f5638eaa13f5`, verifying Git blob identities and SHA256 hashes. No downloaded launchers were executed.

The live-trader cohort is the first 50 accounts in TradingGrader's default risk-adjusted-grade leaderboard snapshot, 7 November 2026. It is not a universal world ranking. All 50 public profiles and their available recent Hyperliquid fills were reviewed: 91,020 fill rows, capped at 2,000 per account. Forty accounts had long and short fills; eight had at least 80% maker fills. These partial fills do not establish closed-trade win rates, exact algorithms, ownership, or independently audited profits. The leaderboard's profitable-days column is not a trade win rate.

Public equity-trader disclosures were also reviewed through Kinfo's podcast and Qullamaggie's own setup descriptions. They point toward catalyst momentum, squeeze/VWAP behavior, and longer trend trades. Exact news, float, dilution, intraday timing, short inventory and option data are not available in a daily OHLC-only test. These discretionary methods were not represented as reproduced bots.

## Fixed-rule research

The independent `app.strategy_lab.broad_strategy_lab` engine tested 15 source-inspired families, three fixed variants each, with Tuesday equity-only. This is 171 market/timeframe cases on current Kraken USD and ten-year ETF proxies, then 126 crypto cases on pinned long Binance USDT history. These rules are Atlas research adaptations, not replications of a leaderboard trader or an author's reported return.

There were 1,692 development/validation cost-segment replays (936 initial and 756 supplemental), followed by explicitly labeled selected or exploratory later-period tests. Two existing pinned daily source adaptations were also checked. Followup protocols were frozen before their new outcomes; they followed the initial screen and must not be marketed as pristine prospective out-of-sample evidence. Original failed selections remain recorded.

Next-open signals, adverse slippage, fees on both sides, shared cash, no leverage, stop gaps at the worse open, and stop-first same-bar ambiguity are explicit. Open holdings are marked and never counted as wins. Each chronological segment starts with independent cash. Each symbol must have 200 bars before eligibility. Equity OHLC is uniformly adjusted using the adjusted-close ratio: a total-return research proxy, not actual historical executable prices. The universe comprises current major coins and ETFs; selection/survivorship bias is not repaired.

## Findings

All dollar results below start with a $10,000 simulation wallet and are after modeled trading fees/slippage, before Grok, compute, taxes or other overhead. Separate segment results are not a continuously compounded account.

| Candidate and later period | Closed wins/trades | Win rate | Marked net | Profit factor | Meaning |
| --- | ---: | ---: | ---: | ---: | --- |
| RSI2_SPY_REGIME daily, 2024-07-19–2026-11-06 | 84/120 | 70.0% | $260.25 | 1.58 | Best equity paper-research lead; 5bp fee + 5bp slip per side; 2 open holdings |
| Same regime, 10bp fee + 5bp slip | 81/120 | 67.5% | $153.12 | 1.38 | Remains profitable under cost stress |
| CONNORS_RSI2 daily, same period | 187/262 | 71.4% | $745.42 | 1.74 | Development and validation lost money; recent result alone does not establish stability |
| ROC3_PULLBACK:0.08 hourly crypto, 2023-06-22–2025-05-30 | 20/31 | 64.5% | $408.73 | 1.70 | Binance USDT history; 0.40% fee + 0.05% slip per side; about 1.33 entries/month |
| Same crypto, 0.80% fee + 0.05% slip | 18/31 | 58.1% | -$13.73 | 0.98 | Fails current entry-tier taker cost stress; not Kraken-qualified |
| EMA_CROSS:[10,50] daily equity control, 2024-07-19–2026-11-06 | 27/66 | 40.9% | $1,540.88 | 2.07 | More profit with lower win rate; does not meet the requested 60% target |

The RSI2 regime's development/validation/later win rates were 69.2%, 58.7%, and 70.0%. All three segments were profitable at both modeled equity cost levels, but the validation win rate misses the strict 60% gate. It is a research candidate, not a proven 60%-minimum system. It buys a completed daily RSI(2)<5 pullback above its own SMA200 while SPY is above SMA200, excludes SPY itself, and exits next-open after RSI>60 or a close-based 10% backstop. The existing source adaptation uses $1,000 fixed stakes and integer shares; it has not been attached to Warrior or Scalper.

The original broad-study equity selection, Williams %R -90, won 60.5% but had only 1.02 profit factor and $78.09 marked profit. No case passed all baseline gates and later-period checks. That failure has not been replaced by a better-looking followup result.

Kraken's official fee page updated 30 October 2026 lists entry-tier spot fees of 0.40% maker and 0.80% taker. Actual tiers depend on account assets/volume and are unknown here. A marketable limit order can be a taker. The earlier 0.40% paper fee is one scenario, not verified current taker pricing. Higher fees reduce profitability; they do not explain the previous absence of BUY signals.

## Atlas decision

Keep NFI's original 88.9% historical result separate from these new families. Prioritize an isolated daily RSI2-regime paper comparison and retain the hourly crypto pullback as a fee-sensitive, low-frequency watchlist candidate. Do not substitute these daily/hourly rules for minute-by-minute Warrior/Scalper behavior or promise trades during every session. No candidate is automatically promoted and no Grok involvement is presumed to improve its expectancy.

The new module is offline-only. It does not import a provider, broker or order path. Existing running workers, launchers, source locks and credentials are unchanged. No subscription, paid model call or order was made. Windows PowerShell and desktop execution were not tested on this Linux research host.

Run a prepared study with:

```text
python -m app.strategy_lab.broad_strategy_lab --root PATH_TO_PREPARED_STUDY
```

The directory needs `protocol.json` and `data/{market}-{symbol}-{timeframe}.json`. Each row has UTC `date`, `open`, `high`, `low`, `close`, `volume`. Use a new directory per study: frozen selections refuse replacement. Protocol and input hashes, source catalogues and results are in the accompanying research evidence bundle. Current data snapshots are included; long source candles are pinned by immutable Git identities. Ten new behavioral tests and four existing source-candidate tests passed using isolated research tests; the full desktop suite was not run.

## Sources

- https://www.tradinggrader.com/leaderboard
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
- https://kinfo.com/podcast
- https://qullamaggie.com/my-3-timeless-setups-that-have-made-me-tens-of-millions
- https://alvarezquanttrading.com/blog/internal-bar-strength-for-mean-reversion/
- https://kjtradingsystems.com/walk.html
- https://support.kraken.com/articles/cross-platform-fee-tier-changes
- https://github.com/freqtrade/freqtrade-strategies/tree/f3340ce11f5bdf62f598522e64d1f5638eaa13f5
- https://github.com/DigiTuccar/HistoricalDataForTradeBacktest/tree/553525cf23aa1af1155aa64d071ab1ddd5f940c1
- Existing source rules: `app/strategy_research/source-candidates.json`

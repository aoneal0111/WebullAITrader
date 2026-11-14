# Crypto reliability and fixed strategy comparison

October 23, 2026. This change repairs data handling and adds an offline,
reproducible comparison. It does not authorize a strategy to place orders.
A public author's win percentage is not an Atlas result.

## Provider repairs

The official SDK raises errors containing `http_status`; previously the adapter
classified only returned HTTP responses. Raised permission, throttling and
business errors could consequently appear as generic transport failures.
The adapter now handles both forms, avoids retries on permanent rejections,
backs off for 60 seconds on HTTP 429, and records only operation, category,
HTTP status and attempt. Provider bodies, messages and request IDs are excluded.
HTTP 417 is a business error unless an explicit known unsupported-symbol code
identifies the pair problem. Permission failures require entitlement/configuration
resolution; these changes cannot supply a missing subscription.

Quotes lacking provider timestamps are rejected. Receipt time must not make an
undated observation fresh. Existing timestamp, tradability and risk safeguards
continue to apply. No GUI or provider account was started during verification.

Official snapshot limits remain 20 symbols per request and the conservative
one-request-per-second pacing:
https://developer.webull.com/apis/docs/reference/crypto-market-data/

## Source review and independent adaptations

The research review covered six primary repositories, including their relevant
source paths, rather than treating popularity as performance evidence:

| Repository | Reviewed revision | Role in this comparison |
| --- | --- | --- |
| jesse-ai/example-strategies | 7c91e0a37bf62165790120d730442e4f6eb00364 | RSI2, Donchian, SimpleBollinger rules |
| freqtrade/freqtrade-strategies | f3340ce11f5bdf62f598522e64d1f5638eaa13f5 | BbandRsi and other Bollinger examples |
| iterativv/NostalgiaForInfinity | 1328a2f3301be02ade7862a4bdf73a0fca751fb5 | Large strategy suite; author tests are not independent Atlas results |
| Drakkar-Software/OctoBot | c45fdc2c95c5bd3d649251fa90c327e2e8f86b00 | Framework review, no adopted performance claim |
| jesse-ai/jesse | 9a3ebe075d9d0af5f012f921f46c2286f234af68 | Framework and indicator conventions |
| freqtrade/freqtrade | c051e9065c5b677f03817dd1f83cb980af1ed684 | Framework, backtest limitations and lookahead guidance |

Source locations:
https://github.com/jesse-ai/example-strategies/tree/7c91e0a37bf62165790120d730442e4f6eb00364
https://github.com/freqtrade/freqtrade-strategies/tree/f3340ce11f5bdf62f598522e64d1f5638eaa13f5
https://www.freqtrade.io/en/stable/lookahead-analysis/

No third-party strategy source is imported or executed. The implementations use
independently expressed indicator rules, bounded sizing and explicit fills.
The Freqtrade examples are GPL-3.0; their source is not incorporated into Atlas.
Jesse's example repository is MIT. These rules are adaptations, not engine parity.

| Candidate | Completed-close entry | Completed-close exit |
| --- | --- | --- |
| RSI2_TREND | Close above SMA200 and Wilder RSI2 at most 10 | Close above SMA5 |
| DONCHIAN20_TREND | Close above SMA200 and preceding 20 highs | Close below preceding 20 lows |
| BB_RSI14_REVERSION | Wilder RSI14 below 30 and close below typical-price BB20 lower band | RSI14 above 70, or 10% gross price target |
| BB_BREAKOUT_CLOUD | Close above HL2 BB20 upper band and displaced 9/26/52 cloud | Close below HL2 BB20 middle band |

All variants use a fixed stop below the signal bar's last 20 lows, multiplied by
0.9995, and at most 10 bars for RSI2 or 30 bars otherwise. These stops/timeouts,
spot-only direction, next-open execution, 6h/daily timeframes and risk caps change
the originals. In particular, BbandRsi's original 25% stop and one-hour timeframe
are not reproduced. Its bands use sample standard deviation; the HL2 candidate
uses population deviation. Cloud values visible now come from 26 bars earlier.

## Frozen experiment

`tools/screen-crypto-public-history.py --output NEW_DIRECTORY` uses only public
Coinbase Exchange candles, without credentials, accounts or execution clients.
Run from the repository using its Python environment. Output directory must be
new. Dates, universe, candidates and costs are fixed in source, not optimized
against the result. Raw response files and their SHA-256 hashes accompany outputs.

- BTC-USD and ETH-USD; 6h candles from 2022-01-01 through 2026-10-23 exclusive.
- Daily candles require exactly four contiguous complete UTC six-hour candles.
- API windows contain at most 300 returned candles, response size is bounded,
  requests have timeouts and at most three attempts. Out-of-range rows are filtered.
- Missing, invalid, duplicate/conflicting or incomplete history aborts the screen.
  No interpolation or synthetic flat candles are used.
- Development: 2022–2023. Later checks: 2024, 2025, 2026 to October 22.
- Both costs: fee 0.1% and adverse slippage 0.1% per side; stress doubles each.
  These are assumptions, not a verified Webull fee schedule or observed spreads.
- Each pair/window starts flat with its own $10,000 hypothetical wallet;
  0.5% initial equity risk including modeled stop costs, maximum 20% allocation.
  Pair results must not be added together as a shared-capital portfolio.
- Completed-bar signals fill at the next open. Stops use the worse opening price
  on gaps, and precede targets on an ambiguous candle. No same-bar re-entry.
- Final open positions are marked after modeled liquidation costs, excluded from
  closed-trade win rates. Drawdown is measured from close/liquidation marks and
  does not claim tick-level or intrabar maximum drawdown.

All 128 pair/timeframe/candidate/window/cost results are retained. A frozen
research gate requires at least 100 aggregate later normal-cost trades and at
least 55% wins; every later pair/year/cost cell must have at least 10 trades,
positive marked return, profit factor at least 1.2 and marked drawdown at most
15%. A passing screen grants research eligibility only. Execution remains false.

Coinbase documents incomplete intervals and pagination requirements:
https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles

This is an exploratory historical comparison. These historical periods were
available before this experiment; they are not a prospective untouched test.
The six-hour and daily comparison does not measure Atlas's existing one-minute
`CRYPTO_RANGE_EMA_PAPER_V1` against identical data. Historical OHLCV cannot prove
quote freshness, current spread, depth, Webull availability or executable fills.
Existing research detectors are retained until an equivalent comparison supports
replacement. No DCA, leverage, shorting or averaging down is introduced.

## Verification

Tests cover signal prefix invariance, an independent scalar RSI oracle, next-open
fills, both-side fees, risk/allocation bounds, entry-bar stops/targets, worse gap
fills, stop-before-target ambiguity, partial/future bars, missing history,
complete-day aggregation and complete candidate reporting. Crypto provider,
runtime, discovery and authority tests cover regression behavior.

# Broader source review and native oversold candidate

The next independently tested component is nateemma/strategies OversoldReversion.
It made positive simulated net PnL in all three tested periods at each of three
fee assumptions. It does not demonstrate daily profitability or solve daily entry
frequency. No installed worker, account setting, wallet, Grok authority or trading
permission changed.

## Scope of the search

Six GitHub repository searches returned 108 metadata records representing 101
distinct repositories. This is discovery, not 101 code audits or backtests.
Selected primary sources covered momentum baskets, oversold reversion, trend
following, statistical arbitrage, funding carry and market making. Source code,
configuration and published native logs were read for the reversion candidate.
The arbitrage repository's advertised 95% win rate was not independently reproduced;
its multi-venue futures requirements differ from the existing spot workers.

## Native results

Unchanged executable strategy logic at source commit
`9bb7dab5df53203e69f60336749551a40767af0b`,
`Reversion/OversoldReversion.py`, blob
`97fd08bb3a3a2e46be388eb920ba54af7f093b3c`.
The downloaded executable had one additional terminal newline, verified against
the upstream blob after removing that byte. No indicators or thresholds changed.

Native Freqtrade 2026.9, CCXT 4.5.84, independent 10,000 USDT wallets per run,
12 slots, hourly Binance candles, 14 of the author's 15 configured pairs. BNB was
excluded because the existing pinned historical lock has no BNB candle file.
ONE remains included. Author source configuration uses BinanceUS; this test uses
Binance history. These are new tests, not reproduction of the author's publication.

Results at **0.4% fees per side**, before spread, slippage, hosting and data costs:

| Period | Closed | Wins | Win rate | Net USDT | Days with entries |
|---|---:|---:|---:|---:|---:|
| 2023 | 4 | 4 | 100.0% | 238.64 | 3 |
| 2024 | 13 | 9 | 69.2% | 661.37 | 8 |
| Jan–Apr 2025 | 31 | 17 | 54.8% | 687.10 | 16 |

Pooled: 30 wins / 48 closed trades = **62.5%**. The sum of the three independent
period PnLs is 1,587.11 USDT; this is not one compounded portfolio return. The
2023 sample is particularly small. All trades exited through the source 72-hour
time exit; no terminal force exits were excluded from these results.

At **0.1% per side**, pooled wins were 32/48 (66.7%) and the sum of independent
period PnLs was 1,856.64 USDT. At **0.8% per side**, pooled wins were 29/48
(60.4%) and summed PnL was 1,232.16 USDT. Every period remained positive, but
the 2025 win rate fell to 51.6% at the higher fee. Repeating fee scenarios does
not create additional independent trades.

There are 851 calendar days across the three periods. As an illustrative
overhead scenario, 2 USDT/day would subtract 1,702 USDT: the 0.4% fee result
would become -114.89 USDT. Actual operating costs were not established.

## Why this cannot simply be activated

- The source requires RSI crossing below 30 while price is over 30% below a
  1,200-hour SMA. These are rare crash/dislocation entries, held for 72 hours.
- Source warmup is 1,250 hourly candles. Kraken's public OHLC endpoint returns
  at most 720 latest entries including an unfinished candle. A separately verified
  durable history preload is necessary; lowering the warmup would change readiness
  and cannot supply the missing 50-day SMA history.
- Current Kraken USD candle and execution validation has not been performed for
  this candidate. The test uses recorded December 2025 exchange metadata, not
  verified historical execution rules.
- Native OHLC fill assumptions and candle-volume caps do not validate order-book
  queue position, slippage or live liquidity. No orders were submitted.

## Validity checks

Nine native runs completed with zero strategy-wrapper warning/error callbacks.
All 14 historical files matched their pinned Git blob identities. There were
126 prefix-causality checks across indicators and entry signals with no mismatch.
The source history contains gaps: each selected pair has a March 24, 2023 hourly
gap within the tested/warmup span. Freqtrade uses its standard missing-candle
handling. No source entry in this test occurred in the following 50-day SMA
window. The original files were preserved. No independent raw exchange feed audit
or native lookahead-analysis command was claimed.

## Decision

Retain as a research portfolio candidate. Do not describe it as a daily-income
bot, a proven future 60% winner, or a ready Kraken deployment. Existing crypto
workers remain unchanged. Further work should measure combined portfolio activity
and net exposure, supply verified Kraken historical warmup, and validate execution
costs before adding another worker.

Primary references:

- https://github.com/nateemma/strategies/tree/9bb7dab5df53203e69f60336749551a40767af0b/Reversion
- https://github.com/nateemma/strategies/blob/9bb7dab5df53203e69f60336749551a40767af0b/config/config_reversion_1h.json
- https://docs.kraken.com/api-reference/market-data/get-ohlc-data
- https://www.kraken.com/pro/fees
- https://github.com/abailey81/Crypto-Statistical-Arbitrage/tree/44b718e4a4ef875ea12dfda30370ab1cc8b54a26
- https://hummingbot.org/strategies/v1-strategies/pure-market-making/

Kraken's published entry tier at review time is 0.4% maker / 0.8% taker.
The user's actual tier is unverified. Marketable limit orders cannot simply
be assigned maker fees. Lower fees alone do not establish a profitable strategy.

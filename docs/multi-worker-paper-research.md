# Shared-wallet multi-worker research

The first server-side test completed November 6, 2026. This is an offline crypto
architecture experiment using two existing frozen Atlas hypotheses,
`RSI2_TREND` and `DONCHIAN20_TREND`. It does not reproduce the Instagram bot,
NFI, native Warrior, native cent Scalper, or a historical Grok decision stream.
It is not connected to Atlas order submission. No paid model calls were made.

## Data and worker count

The first 50 alphabetical daily USDT spot files from the existing pinned NFI
history lock were selected before inspecting results. Downloaded bytes were
verified against all 50 locked Git blob identities. The source is
[DigiTuccar/HistoricalDataForTradeBacktest at 553525cf](https://github.com/DigiTuccar/HistoricalDataForTradeBacktest/tree/553525cf23aa1af1155aa64d071ab1ddd5f940c1).
The original NFI author file set and strategy are not modified.

Each eligible symbol has two strategy workers, sharing the same candles. BNX
has a gap in the required 2022–2024 daily calendar and is explicitly excluded,
without replacement or invented candles. Therefore the real-data test uses
49 symbols and **98 workers**. Separate synthetic tests exercise exactly 100
workers, including duplicate workers trying to buy the same symbol.

There are two independent strategy families, not 98 independent strategies.
The selected historical universe has selection/survivorship bias. These
previously available dates are not certified untouched holdout data.

## Results

Every case starts with a separate 10,000 USDT shared wallet. Signals use only
completed candles and fill at the following open. Normal assumed costs are
0.1% commission and 0.1% adverse slippage per side; stress doubles both.
Fractional spot quantities are simulated; actual venue rounding, minimum orders,
spread/depth, funding, Grok and hosting costs are not modeled.

| Period | Costs | Closed trades | Closed win rate | Realized net | Marked net, including open positions | Close drawdown |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2023 | Normal | 287 | 41.46% | −737.51 | +0.93 | 30.06% |
| 2023 | Double | 287 | 41.11% | −1,254.35 | −601.26 | 32.92% |
| 2024 | Normal | 451 | 45.23% | +4,480.03 | +4,614.88 | 29.52% |
| 2024 | Double | 455 | 44.18% | +3,334.32 | +3,453.26 | 32.18% |

These cases do not meet the requested 60% win-rate target. Positive 2024 net
profit coexists with substantial drawdown and weak 2023 performance. There
were 42 terminal open positions in each 2023 case and six in each 2024 case;
open marks are not counted as closed winning trades. Daily close drawdown
understates possible intraday drawdown. Nothing is automatically promoted.

The two normal-cost windows and both stress replays completed in approximately
11 seconds after loading history, including shared signal computation, on the
server test environment. This is not a live latency or Grok performance result.

The machine-readable report, including file hashes, exclusions, family results,
cost assumptions and accounting residuals, is
[multi-worker-backtest-2026-11-06.json](multi-worker-backtest-2026-11-06.json).
Full local replay output additionally includes trades, per-worker results and
equity curves. Marked net equals realized plus unrealized net within 1e-6 USDT.

## Coordination and failure checks

One shared cash balance funds every entry. One symbol has at most one owner;
worker ID supplies disclosed fixed priority. Exit decisions belong to the
entry worker. Duplicate workers cannot multiply buying power or profits.
Only exits executable at the open can release capital for that open's entries;
intrabar stop proceeds cannot fund earlier entries. Stops gap to the worse
open. Open holdings reserve hypothetical exit costs. Terminal pending signals
never become fabricated fills. No cash borrowing or automatic strategy ranking
is introduced. Normal experiment sizing is 0.5% of opening liquidation equity
at the planned stop, capped at 10% per entry and available cash; gaps can exceed
planned loss. Many simultaneous positions can produce substantial aggregate
risk, as the drawdowns show.

Tests cover cash/PnL reconciliation, 100-worker contention, duplicate entry
suppression, ownership, missing/unaligned bars, invalid economics, next-open
causality, future-bar perturbation, gap stops, higher costs, terminal open
positions and source corruption. Existing Warrior/Grok, account sizing,
extended-session and quote-confirmation tests are run separately from this
research module. Runtime broker connectivity and fresh live quotes cannot be
certified by offline tests.

## Remaining forward-test blockers

- There is no live/shadow adapter connecting this portfolio to the GUI or its
  canonical account gateway. The current V6 Warrior/Grok and native Scalper
  remain the installed forward-paper strategies.
- The existing stale bid/ask issue remains unresolved. A candle backtest cannot
  establish current quote entitlement, timestamps or fill availability.
- The screenshot's "$40K", "dead volume", "vertical" and seller/buyer ratio
  need precise definitions and suitable historical/live inputs. Daily candles
  cannot reconstruct order-book imbalance. No missing flow data is assumed.
- No valid evidence of the creator's profit ledger was supplied. Two losses
  do not trigger automatic deletion of a strategy or its history here.
- Grok news/scout coordination, response deadlines and actual paid operating
  costs need their own forward observation. This replay makes zero Grok calls.

## Optional local reproduction

`tools/test-multi-worker-portfolio.ps1` creates a detached research checkout,
checks tests, creates a separate Python environment inheriting the existing
Atlas libraries, installs pinned Arrow there, downloads the 50 pinned files
and writes a complete offline replay report under a new AtlasResearch folder.
It does not install or start these workers in the desktop, change the existing
trial, enable trading, start a Grok observer or modify installed settings.
Use an exact reviewed revision. Existing Atlas must continue to be launched
manually through the user's prepared PAPER launcher.

# Equity entry overhaul: qualified strategies only

October 23, 2026. This release removes the old Warrior/Quick Scalper policies'
permission to create new desktop PAPER BUYs. It does not claim a replacement is
profitable. No replacement has been promoted. The GUI explicitly says equity
BUYs are paused while position protection remains available.

## Deployed behavior

A mandatory dispatch service checks release qualification independently of the
optional controller and Grok switches. Warrior/Scalper original entries, re-arms,
add-ons, replacements and prepared AI submissions cannot bypass the desktop BUY
boundary. The bridge also rejects unqualified preparation before creating an
order or queuing AI review. Direct desktop PAPER dispatch uses the same boundary.
SELL and cancellation requests continue through their existing service and all
existing account, freshness, ownership, halt and structural-risk checks remain.
No new hard RVOL/spread cutoff is added.

Inherited unqualified working BUYs are cancelled before market delivery starts.
Partial filled exposure remains for normal protection reconciliation; SELL legs
are retained. An uncertain cancellation stops startup rather than allowing the
old BUY into matching. No campaign is reset or historical ledger deleted.
Position recovery recognizes historical sessions separately from today's entry
sessions, so excluding after-hours entries cannot erase a position opened under
an older policy. This does not allow new after-hours or overnight entries.

The empty `RELEASE_PROMOTIONS` registry in `app/equity_overhaul/admission.py` is
intentional. Promotion requires a reviewed code release with exact rule-code,
configuration and evidence hashes. Runtime flags, model responses, asserted win
rates and self-declared approval metadata cannot populate this registry.
The standalone bridge retains its injected mechanical/research interface;
production desktop composition always installs both early and dispatch checks.
Live broker entry paths are outside this PAPER-only change.

Old detectors and management code are retained to read historical lifecycles,
observe candidates and protect existing positions. Deleting that code would
remove recovery/exit capabilities; removing its buying authority addresses the
entry problem directly. Strategy scores and an active scanner do not imply
qualification. No existing policy is silently renamed into a replacement.

## Source review

| Primary source | Evidence reviewed | Decision |
| --- | --- | --- |
| Zarattini, Barbon, Aziz: opening range breakout, SSRN 4729284 | Large historical stock universe; costs and selection matter | Candidate family; not transferable Atlas win-rate proof |
| QuantConnect: Opening Range Breakout for Stocks in Play | Rules, public replication, universe/ATR/relative-volume selection, replication discussion | Independent ORB adaptation; no imported engine or author performance claim |
| Zarattini, Aziz, Barbon: SPY intraday momentum, SSRN 4824172 | Prior 14 sessions' same-time open deviations, VWAP, decision timing and costs | Independent long-only SPY/QQQ adaptation; source's long/short/leverage results not reproduced |
| Concretum author Python backtest tutorial | Public rules, commissions and shifted exposure; hybrid overnight reversal is distinct | No source code copied; no author returns attached to Atlas |
| alpacahq/example-scalping | 20-minute SMA crossover and outstanding sell limits | Rejected: author explicitly warns of indefinite sell waits and loss exceeding accumulated profits |
| alpacahq/Momentum-Trading-Example | Opening-range/MACD example and end-of-day liquidation | Reviewed as execution example, not verified profitable system |
| alpacahq/example-hftish | Quote/order-book imbalance and one-cent moves | Deferred: minute OHLCV cannot validate depth, queue priority or such fills |
| Alpaca mean-reversion tutorial (June 2026) | Z-score workflow; illustrative, externally authored content | Hypothesis guidance, not independently verified performance |

Sources:
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729284
- https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/p1
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4824172
- https://concretumgroup.com/wp-content/uploads/2026/02/Beat-the-Market.pdf
- https://concretumgroup.com/backtesting-7-years-of-free-data-beat-the-market-an-effective-intraday-momentum-strategy-for-the-sp500-etf-spy/
- https://github.com/alpacahq/example-scalping
- https://github.com/alpacahq/Momentum-Trading-Example
- https://github.com/alpacahq/example-hftish
- https://alpaca.markets/learn/how-to-build-backtest-mean-reversion-strategy-with-alpaca

A profitable paper or tutorial is a source hypothesis. It is not an independently
verified deployed bot, and paper returns do not establish a high win percentage.
No automated bot, third-party dependency or author strategy code is installed.

## Frozen replacement candidates

Only these three replacement rule definitions are included:

| Policy | Entry | Exit and scope |
| --- | --- | --- |
| ORB5_LONG_V1 / momentum | Bullish first five minutes; first completed close crossing opening high; above session VWAP; 09:35–10:30 Eastern | Opening-low structural stop, 2R target, at most 60 bars, one entry per symbol/session |
| NOISE_BAND_LONG_V1 / momentum | SPY/QQQ only; 14 prior complete sessions' same-minute mean absolute open-relative move; close above upper boundary and VWAP; 30-minute decisions through 15:30 | Prior-five-low hard stop; signal exit when band/VWAP condition fails on a decision clock; maximum two entries and same-session exit |
| VWAP_RECLAIM_SCALP_V1 / scalper | Prior bar tags VWAP; bullish close above prior high and VWAP; rising VWAP and SMA20; 09:45–15:30 | Prior-three-low structural stop, 1R target, five-minute maximum, at most three entries/session |

All stops use a fixed 0.9995 buffer and cannot widen after entry. These are
independently expressed adaptations with substantial differences from the
sources: completed-bar confirmation, long-only direction, no leverage, changed
stops/targets and a fixed liquid universe. VWAP reclaim is a hypothesis without
an independently reproduced profitable source result. None is an active order
producer. Production signal adapters and execution parity require a later
validated release; merely adding a registry entry is insufficient.

## Reproducible exploratory screen

Run `python tools/screen-equity-overhaul.py --output NEW_DIRECTORY` from the repo.
It uses bounded public Yahoo minute history for SPY, QQQ, AAPL, MSFT, NVDA and TSLA
from September 25 to October 23, 2026 exclusive. These dates/universe/rules/costs
were fixed before this screen's results. This short window is not a prospective
holdout, a full-market stock-in-play replication, or enough evidence for promotion.

Regular-session boundaries come from the NYSE calendar, including early closes.
Missing/invalid sessions are excluded and reported, never filled or stitched.
Raw responses, normalized bars and SHA-256 hashes accompany the complete results.
Normal assumed adverse spread/slippage is 5bps per side plus $0.0035/share per
side; stress doubles both. These assumptions are not verified Webull costs,
quoted-side fills or liquidity/market-impact evidence.

Signals use only completed candles, enter at the next open, pay costs on both
sides, use worse opening prices on gap stops, and stop before target on ambiguous
candles. No entry uses the future entry-bar volume as a filter. At most one
position per symbol/policy; exits cannot immediately re-enter on the same bar.
Inputs require complete sessions; no boundary mark is manufactured as a win.
Outputs are per-share/R screening records, not a shared-capital account return.
Short/long P&L, dividends, borrow, queue and partial-fill effects are not modeled.

Every symbol/policy/cost cell is retained. No search for a favorable pair, cost,
period or parameter grants promotion. A subsequent qualification needs sufficient
later date windows, at least 100 reconciled trades per intended policy/universe,
positive net and stress expectancy, useful drawdown limits, portfolio replay and
forward PAPER execution evidence with matched code/config/data fingerprints.
The runtime does not accept self-attested statistics as approval.

## Offline status and verification

`python -m app.equity_overhaul` prints the shipped registry and candidate rules
without starting the GUI, connecting to a provider or placing an order.

Tests verify real desktop composition with the optional controller on and off,
bridge refusal before preparation, direct-dispatch refusal, metadata spoofing,
SELL/cancellation pass-through, inherited BUY quarantine, historical position
recovery, source-time causality, opening-range confirmation, VWAP arithmetic,
noise-band warmup, next-open costs, gap stops and ambiguous-bar ordering.
Existing historical after-hours research fixtures explicitly opt into their old
research session; default desktop entry tests require premarket/regular only.

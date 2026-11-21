# Whole-share Momentum exit accounting

`app/equity_overhaul/whole_share_exit_replay.py` adds a standard-library, offline
accounting component. It has no CLI that starts Atlas, broker access, network
client, policy registration or execution authorization. It does not replace the
existing early-entry screen or automatically consume its old results.

`compare_exit_paths` compares `FULL_2R` and `HALF_1R_HALF_2R` for one identical
already-armed entry trigger, fixed structural stop, whole-share quantity, cost
schedule and complete candle path. Entry selection, point-in-time universe,
known prior close and pre-HOD eligibility remain the caller's responsibility.
This is an accounting component, not a new qualified entry strategy. It belongs
to Momentum research; no Scalper management settings are changed.

- Odd share counts sell floor(quantity/2) at 1R, leaving all other shares for
  the runner. A one-share position uses the full 2R exit without a partial.
- Entry is the worse of the first bar open and a previously armed trigger,
  with declared adverse bps. Gaps through the initial stop do not enter.
- Both targets are based on cost-stressed entry minus the fixed structural stop.
  Planned initial price risk excludes commissions; all commissions enter net PnL.
- Per-share commission and minimum commission are charged as the larger amount
  on each hypothetical order: entry, partial and final exit. No zero-share order
  is charged. Partial fills of one order across many market events are not modeled.
- A known stop wins an ambiguous candle. After a partial, only subsequent
  candles use raw-entry price as the runner stop; it is never backdated into
  the partial candle. Price breakeven is not cost breakeven.
- Stop gaps fill at the worse opening price. Targets fill at their levels
  without favorable opening-gap improvement; declared adverse exit bps still
  apply. These are assumptions, not observed liquidity or actual fills.
- Every minute from the supplied entry bar through the declared session end
  must be present. Even paths that would exit early are rejected if later
  coverage is missing, so both variants retain the same coverage population.
  The caller must independently establish the exchange session and its endpoint.
- The supplied path is bounded to 390 contiguous, aware, exact-minute OHLC
  candles with finite positive prices. Whole-share quantities and costs are
  bounded/validated; input candles are immutable. Intrabar execution timestamps
  are unknown: fill records identify a bar interval, not a measured fill time.

For an invented five-share entry at $10 with a $9 stop, two shares at $11 and
three at raw entry return $2 gross. The full position stopping at $9 loses $5.
With a $1 minimum on each of the entry and two sell orders, the partial variant
instead loses $1 net. A gap in the remaining shares can also overwhelm the
realized partial. None of these invented paths estimates a strategy win rate.

Thirteen focused tests verify inventory/cash accounting for odd and single-share
positions, commission minima, two targets in one candle, stop-first ambiguity,
next-bar stop promotion, adverse gaps, no-entry cases, missing-tail/internal-gap
rejection, session exits, cost stress, paired entry identity and invalid inputs.
The ten coverage and seven metric-semantics tests pass alongside these tests.

No complete representative mover history was added with this component. The
existing private 30-file sample remains insufficient for the full-day comparison.
No profitability or release claim follows from these synthetic checks. Monday's
prepared PAPER baseline, risk controls, protective exits and blocked Grok requests
remain unchanged. No user performance data is included in the repository.

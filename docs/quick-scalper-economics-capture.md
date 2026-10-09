# Quick Scalper execution-economics evidence

This repair aligns entry approval with the executable price path and improves
PAPER forensics. Targets adapt to volatility, execution cost, structural risk,
and stream confirmation. Sizing retains the canonical account/risk boundaries.

## Executable target reachability

The movement proxy comes from observed BID movement. Previously it was
compared only with the target increment above ASK. Approval now requires that
movement cover the distance from current BID to the ASK-anchored target plus
estimated exit slippage. Percentage velocity is also converted at BID.

The threshold follows the current spread, adaptive target, and liquidity cost.
There is no fixed spread or RVOL veto. Stronger movement can
make the same spread executable on a later assessment. Existing hard safety
checks still run before this economic wait condition.

For stream entries, movement evidence is the smaller of the trailing BID range
and the positive BID advance from the first retained distinct provider quote to
the current quote. An earlier high that has already retraced cannot supply the
remaining move. Authoritative confirmation adjusts that advance by the change
from the discovery BID to the execution BID. Candidate/bar snapshots without
stream evidence retain their BID-range proxy.

Per-minute velocities remain diagnostic context; they cannot supply
an unobserved future minute of movement. This avoids multiplying a brief burst
into an unsupported dollar estimate. Larger observed movement can recover at
the same spread without a fixed duration or sample-count veto. Historical range
is still a proxy, not a prediction of the next move or a calibrated win probability.

## Structural risk and stream confirmation

Targets must also cover modeled execution cost plus a reward relative to the
actual ASK-to-structural-stop risk. With the defaults, the required modeled net
reward ranges smoothly from 0.50R with strong confirmation toward 0.75R with
weak confirmation. These are policy defaults, not an empirically calibrated
profitability threshold. A broader stop therefore requires a larger target and
more demonstrated BID movement, rather than silently reducing reward/risk.
The structural stop itself is unchanged.

Stream confidence combines upward price changes, their share of all nonzero
price changes, and elapsed provider time. Upward-update confidence is
`upward / (upward + 1)`; time confidence is `elapsed / (elapsed + 2 seconds)`.
Multiplying these with the upward share gives a bounded confirmation score.
The two-second value is a smooth scale, not a minimum holding or observation
period. A sufficiently large observed short burst can still qualify.

Repeated provider timestamps replace the most recent sample at that instant;
they do not add independent confirmation. Flat-price heartbeats do not increase
the upward-update count. The rolling history retains its 30-second, 64-sample,
512-symbol bounds. Economic waits remain recoverable on the same generation.

## Prospective quote path

The desktop sidecar sends raw QUOTE bid/ask values and their provider timestamp
to the existing execution-path sampler, independently of completed bars and
scanner reference completeness. Only lifecycles seeded by authoritative PAPER
fills are sampled. Missing LAST remains missing; a midpoint is never presented
as an observed trade price.

Existing bounds remain: one sample per provider second, at most 128 active
lifecycles and 20,000 samples per lifecycle. Duplicate/older observations do not
produce another sample. Records retain quote age, stale/future flags, and gap
flags. The full strategy-evaluation path uses the same sampler so it does not
duplicate stream samples. Exceptions in the additional raw-quote capture hook
are contained so that this observational hook cannot interrupt stream handling.

Older runs may contain only the quotes observed at full Warrior evaluation
times. Their minimum/maximum recorded bids are not tick-by-tick extrema. This
change cannot reconstruct missing historical quotes.

## Durable entry economics

New Quick Scalper entry-order metadata carries scalar `scalp_` fields for:

- Confirmed execution BID/ASK, structural stop, risk per share, and target.
- Estimated round-trip execution cost, movement proxy, and required BID movement.
- Calculated net target reward divided by stop risk.
- Provider BID/ASK timestamps and execution-quote confirmation time.
- Trailing range, velocity, and stream sample count/elapsed time when available.
- Directional BID advance, upward/nonzero price-update counts, confirmation score,
  and required modeled net reward/risk.

ASK is stored as `scalp_entry_trigger`. Movement and cost values are policy
estimates, not calibrated profit probabilities or guaranteed execution prices.
Stream sample count/elapsed time describe the discovery observation; timestamps
describe the separate confirmed execution quote. These inputs survive durable
order restoration and can be joined to fill-derived lifecycle P&L.

## Remaining policy validation

The policy uses structural risk and confirmation to select the target, then
approves only when observed movement supports reaching it from BID with
estimated exit cost. This does not establish positive expectancy. Target geometry,
position allocation, and momentum persistence need replay/evaluation against
the recorded evidence before claiming
a profitability improvement. Quote capture and passing tests establish neither
profitability nor realistic live fill/slippage behavior.

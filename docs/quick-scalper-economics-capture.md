# Quick Scalper execution-economics evidence

This repair aligns entry approval with the executable price path and improves
PAPER forensics. Targets and sizing retain their existing adaptive rules.

## Executable target reachability

The movement proxy comes from BID range and BID velocity. Previously it was
compared only with the target increment above ASK. Approval now requires that
movement cover the distance from current BID to the ASK-anchored target plus
estimated exit slippage. Percentage velocity is also converted at BID.

The threshold follows the current spread, adaptive target, and liquidity cost.
There is no new fixed spread, RVOL, or reward/risk veto. Stronger movement can
make the same spread executable on a later assessment. Existing hard safety
checks still run before this economic wait condition.

Entry approval uses the observed short-horizon BID range as its movement
evidence. Per-minute velocities remain diagnostic context; they cannot supply
an unobserved future minute of movement. This avoids multiplying a brief burst
into an unsupported dollar estimate. Larger observed movement can recover at
the same spread without a fixed duration or sample-count veto. Historical range
is still a proxy, not a prediction of the next move or a calibrated win probability.

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

ASK is stored as `scalp_entry_trigger`. Movement and cost values are policy
estimates, not calibrated profit probabilities or guaranteed execution prices.
Stream sample count/elapsed time describe the discovery observation; timestamps
describe the separate confirmed execution quote. These inputs survive durable
order restoration and can be joined to fill-derived lifecycle P&L.

## Remaining policy validation

The policy calculates net reward/risk but approves on movement reaching the
adaptive target from BID with estimated exit cost. This does not establish
positive expectancy. Target geometry, position allocation, and momentum
persistence need replay/evaluation against the recorded evidence before claiming
a profitability improvement. Quote capture and passing tests establish neither
profitability nor realistic live fill/slippage behavior.

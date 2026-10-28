# Recorded Scalper edge and quote replay

The session journal records stream snapshots, the native policy result, paired REST confirmation inputs, and the native policy configuration. The audit uses those existing inputs, not a new feed capture or model opinion. It never changes the recording or the installed app.

## Method

1. Verify the closed journal's sequence/hash chain and final receipt. Recheck each record while replaying so changed input is refused.
2. Require an unchanged recorded policy and exact source-file hashes for `quick_scalper.py` and `quick_scalper_runtime.py`. Refuse mismatched source instead of attributing historical results to different rules.
3. Reconstruct typed Decimal, datetime and immutable configuration fields. Re-run `QuickScalperPolicy.assess` on each recorded stream assessment. Reproduce the native REST confirmation replacement, and compare its coarse baseline result with the recorded result. Tests exercise the real runtime and require the reconstructed confirmation snapshot to match its actual policy input.
4. Report reason counts, initial/confirmed edge margins, bid/ask timestamp regressions, book freshness, confirmation latency, and which native target terms dominate. Include at most ten closest insufficient-edge examples and twelve most-observed symbols. These reports contain private prices and symbols and remain local.

Output reports `source_replay_matches=false` if a decision differs. That is a discrepancy to investigate, not a profitable candidate. No thresholds, timestamps, subscriptions, execution policies, holding periods or risk limits are changed. No Grok/broker/market-data requests occur. The PowerShell wrapper creates a separate reviewed worktree and writes a small report under LocalAppData. It does not merge into installed Atlas, enable a strategy, or launch the GUI.

## Interpretation

The current target move is the maximum of the absolute minimum, a range fraction, a multiple of spread/slippage cost, and stop-distance risk times the required reward plus cost. Required bid movement also bridges the current bid/ask anchors and exit slippage. The policy's expected move is capped by observed bid and ask advances. These are demonstrated **past** movements, not a verified forecast of remaining profit. The audit also separates the two price anchors: the target is above entry ASK, while movement is measured from BID. It reports target-minus-modeled-slippage alongside the native target-minus-full-cost calculation. Their difference is the spread reserve. This exposes conservative reserve versus fill-PnL accounting; it does not assume observed fees, delete reserves, or label the alternate arithmetic as a new backtest. The audit exposes that relationship without extrapolating velocity or claiming that deleting a gate would make trades profitable.

A returning REST response need not have a newer provider book time. A regressed REST timestamp is useful diagnostic evidence, but keeping an already stale stream quote cannot make an entry valid. The audit separately counts cases in which the original stream book would still have been fresh at confirmation. Timestamp advancement and executable-policy success are different measurements: the older `quick_scalper_stale_preview_refresh_advanced` counter increments only when confirmation becomes executable; it does not count every newer timestamp.

The official [Webull streaming guide](https://developer.webull.com/apis/docs/market-data-api/data-streaming-api/), checked October 28, 2026, separates QUOTE order-book data from SNAPSHOT and TICK data and documents a timestamp on the shared message envelope. The [stock snapshot reference](https://developer.webull.com/apis/docs/reference/snapshot/) describes on-demand snapshots. Neither a fresh last trade nor local HTTP receipt time is sufficient evidence to rewrite an old provider book timestamp. This journal contains normalized data, not raw provider messages or account entitlement proof; further feed-contract or quote-source validation may still be needed.

No public or private PnL is inferred from reason counts, repeated snapshots or closest-edge examples. A candidate strategy change must be evaluated as a separately frozen adaptation with costs and exit rules, then tested on separate history and forward paper evidence.

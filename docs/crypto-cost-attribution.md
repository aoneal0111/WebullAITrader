# Crypto cost attribution and venue boundary

Checked October 28, 2026 against official public pages:

- [Kraken cross-platform fee tiers](https://support.kraken.com/articles/cross-platform-fee-tier-changes), updated October 19, states the fee-tier changes took effect July 9, 2026. Current Tier 1 spot maker is 0.40% and taker is 0.80% per side. Actual fees depend on the account, order and pair. The frozen candidate screen's 0.40% / 0.60% scenarios are assumptions, not a verified current entry-level taker schedule. Do not rewrite that completed experiment or its selection after seeing results.
- [Webull crypto Fees and Limits](https://www.webull.com/help/faq/11091-Fees-Limits) describes a 1% markup/markdown on each buy and sell, embedded in the quote, with stablecoins exempt. This public help description does not attest the operator's API/account/provider route. The [API guide](https://developer.webull.com/apis/docs/trade-api/crypto/) links to pricing rather than giving a separate account-specific cost. Confirm the actual executable quote and trade confirmation before using a venue cost model. Do not double-charge the embedded spread if using already-adjusted bid/ask.

With symmetric friction c, the underlying price return needed just to break even is (1+c)/(1-c)-1. This applies to commission-on-notional fills and to symmetric embedded markup/markdown, despite different accounting mechanisms. At 0.8% per side it is about 1.613%; at 1% it is about 2.020%. Extra market spread and slippage raise the hurdle. These are cost arithmetic, not tested strategy profits.

The new stdlib-only module reads an existing complete candidate screen and its native development results. It reconciles unlevered long spot fill PnL into gross price profit and modeled commissions, reports average net winners/losers, descriptive break-even win rate at those observed payoffs, and exit-reason totals. It rejects unreconciled accounting and never opens installed databases, network connections, Grok, or trading APIs. Outputs contain private simulated performance and must remain local.

Changing costs can alter ROI exits, accepted signals and the whole trade path. This tool deliberately does not subtract a fee difference from old profits and call it a new backtest. A future venue-specific comparison must freeze a new cost protocol and run the native engine again. September performance is not opened by this audit. Existing lab/Atlas policies and protection remain unchanged.

Run the supplied PowerShell wrapper with the exact completed screen directory and reviewed revision. It creates a separate source worktree, verifies focused accounting tests, writes a report in a new private audit directory, and never starts a bot.

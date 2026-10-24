# Actual provider inputs through a disabled temporary crypto worker path

`check-weekend-observer.ps1 -CryptoProviderWorker` joins the existing bounded
bar/quote checks to the existing crypto subprocess, parent controller, shared
allocation controller and PAPER simulator. It is a separate diagnostic, not
desktop wiring or an installed application update.

The PowerShell runner checks PAPER mode, live disabled and Grok order-review
disabled before proceeding. It uses a disposable pinned source worktree and
the installed Python environment. The new online mode requires the explicit
`--online-read --provider-worker` CLI flags and constructs only a market-data
client. It cannot be combined with the other crypto modes.

The diagnostic starts a pure worker, then performs three BTC/USD and ETH/USD
history-then-quote cycles 65 seconds apart. Scheduling is bounded to 180 seconds;
individual requests and worker replies have their own timeouts. Normal execution
takes about two minutes. All previous history coverage, revisions, causal
availability, progression, ownership, freshness and clock checks still apply.

Only clean native history frames reach the worker. The parent verifies the child
reply against the unchanged pure signal function. At handoff and completion,
the diagnostic uses current time rather than freezing response latency out of
the quote-age calculation. A pass requires each of the six worker assessments
to complete within two seconds with a quote still no older than two seconds.
It does not fetch an additional quote after the worker assessment.

New temporary SQLite stores contain an invented diagnostic account. Router
entries remain disabled throughout. There is no call to `set_enabled(True)`;
an additional authorization callback always refuses. A real qualifying signal
must return `ENTRIES_DISABLED`, while a no-setup result may pass if its inputs
and handoff are valid. Six BUYs are neither expected nor required.

The temporary ledger must have zero positions, fills, active allocations and
authorization attempts after every handoff and at completion. Worker and ledger
handles close before temporary-directory cleanup. Installed databases, strategy
settings and PAPER campaign are never opened or changed. No broker or Grok
client, desktop runtime, subscription purchase, or credential entry is involved.

The small local `atlas-crypto-provider-worker-*.json` contains input timings,
worker results, source/settings fingerprints and empty-ledger checks. It omits
raw prices, proposals, credentials and response bodies. Failed or partial input
cycles are retained and do not become a passing result after recovery.

This verifies a bounded data-to-worker-to-disabled-parent path. It does not test
filled trades with actual prices, protective exits on an actual open position,
continuous runtime behavior, profitability, the installed desktop configuration,
or online Grok readiness. The previous synthetic check exercises local fills,
durable recovery and stop/target exits separately. Neither check enables entries.

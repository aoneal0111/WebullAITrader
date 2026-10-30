# Explicit Grok PAPER execution pilot

This mode enables an order-review path. It is not observer-only. It reuses the
installed CENT_EARLY_V2 experiment, the canonical equity PAPER gateway and the
existing crypto simulator. It does not submit Webull or exchange broker orders.
No extra bot repository or second order owner is installed.

| Component | Evidence and decisions | Execution authority |
|---|---|---|
| Warrior and Quick Scalper | A combined review evaluates their separate native candidates, sizing, stops, targets and up to 32 retained completed native bars. Missing history is labeled unavailable. | Master may select supplied candidates. Models cannot invent an equity signal, rewrite its price/shares/stop, or bypass native gates. |
| Crypto | Existing Grok crypto proposal provider receives discovered BTC/USD and ETH/USD observations, available completed provider bars, local exposure and trial limits. | BUY/SELL proposals pass through existing shared allocation and the local durable simulator. The visible crypto module and AI-entry switches remain manual. |
| Execution | Existing controller rechecks account, ownership, capital, risk, current native authorization and fresh quotes after the model returns. | Successful equity submissions are adopted by native position management. Crypto uses its existing protection thread and durable ledger. |
| Backtest review | Existing LearningJournal imports a raw `ENGINE_COMPARISON_V1` experiment and reviews paired, cost-inclusive evidence. | Research nominations only. It does not generate historical prices, recompute a strategy backtest, place orders or promote policies. With no valid imported comparison it remains IDLE without a paid call. |

## Bounds and accounting

The operator launches a new visible Atlas GUI manually. This creates a lease of
at most 30 minutes, shortened to the existing parent experiment expiry. Startup
time consumes lease time. Restarting does not renew it. The original experiment
ID, expiry, entry-attempt database and native strategy parameters are preserved.

The original 50 entry-attempt / $5,000 cumulative entry-notional budget is shared
by native equity and crypto reservations. Equity retains 10 shares, $100 position
and $1 modeled initial risk limits. Crypto additionally caps reserved capital at
$100 and modeled initial risk at $1. Fees and slippage count in reservation and
risk calculations; a proposed $100 notional may therefore be refused. These are
modeled limits, not guarantees of realized loss or fill prices. Crypto prompt
context asks for at most $99 entry notional to leave room within reserved capital;
the ledger still independently validates costs and risk.

One dedicated durable request ledger limits all five roles (Warrior, Scalper,
Crypto, Master and Learning) to 20 paid attempts combined, with a persistent
60-second per-role cooldown. In this explicit pilot, one combined Master request
replaces the sequential equity engine-then-Master calls. The default non-pilot
suite retains its separate advisers. Crypto and Learning calls remain separate. Prior
retained Grok intentions summaries in the same trial count against this total.
Missing final accounting blocks preparation. Failed, timed-out and crashed calls
consume reservations. No automatic retries or allowance resets are provided.
Per-request records retain the client's cumulative usage snapshot; do not sum
those snapshots as independent token totals.

The shared budget constrains calls, not dollars. Model pricing and provider
billing remain external. This pilot makes no subscription purchase.

## Compact context and latency measurements

The pilot caps decision reasons at 240 characters. The model, endpoint and
2,048-token output ceiling are unchanged until a lower ceiling is validated
online. An untested low total-output ceiling can produce incomplete responses
instead of usable decisions. The independent backtest reviewer keeps its normal
analytical response format. Incomplete responses are refused with no retry.

For the exact verified Grok 4.5/4.6/4.7 model IDs, explicit execution calls now
request low reasoning effort. Default non-pilot and backtest calls retain their
provider defaults. See [reasoning verification and the disconnected one-call
probe](grok-reasoning-latency.md). This change is not an online latency guarantee.

Completed candle windows are packed losslessly as named columns, common metadata
and rows. Original prices, timestamps and provenance remain present. A bounded
32-window local cache reuses only these encoded bar windows, keyed by their full
contents. It does not cache BUY/SELL decisions, quotes, account state, candidate
IDs or authorizations. Changed candles get a new key. This is not provider-side
prompt caching and does not eliminate the model request. The current requests
ledger retains the exact packed context sent to Grok; the daily journal retains
the full captured market evidence. Logs are for audit and research, not an
instruction to upload an entire day's history with every decision.

Each new request records its own measured duration and original/sent input bytes.
`Show-Grok-Latency.ps1` reads the dedicated ledger without API calls and reports
median, p95, maximum, failure/unfinalized counts and input sizes. Older records
without per-request timing remain unknown. It exports no prompts, answers,
positions or trading PnL. Input-byte reduction and fewer model calls are verified
offline; actual online response speed and decision quality still need measurement.

This update does not turn Grok into an independent whole-equity strategy or a
free-form position manager. It reviews the existing native entry candidates;
native deterministic exits continue to manage equity positions. Crypto retains
its own bounded BUY/SELL proposal path. Broader model-owned equity proposal and
management behavior requires its own explicit PAPER experiment and evidence.

## Freshness, latency and protection

No model call runs on the native GUI/strategy owner lane. This change does not
claim to make the configured model respond within two seconds. During the wait,
the native equity lifecycle must remain valid and receive current authorization;
the controller then requires current quotes. Stale or expired advice is refused.
Sparse quotes can still prevent trading; freshness limits are not relaxed.

Crypto refreshes only already discovered/owned pairs after BUY/SELL advice and
requires provider timestamps at most two seconds old. The independent protection
worker also refreshes owned pairs, once per five-second cycle, without waiting
for Grok. This is sampled simulator protection, not an exchange-resident stop or
guaranteed continuous price coverage. Provider errors or stale quotes can delay
an exit. Existing risk, spread, plan and ownership checks remain in force.

Lease expiry, the pause script, or request exhaustion prevents further Grok
admission. It does not stop native equity protection or the crypto protection
worker. Keep Atlas, its crypto module and market data running while virtual
positions need protection. Do not kill processes to end this experiment.

## Installation and operator steps

1. Close Atlas and old Grok intentions/reviewer workers normally. Run
   `tools/prepare-grok-execution-trial.ps1` at the reviewed revision with the
   existing trial directory. It verifies local configuration without API calls,
   runs offline tests, creates a backup branch and installs by fast-forward only.
   Tracked user edits or changed trial/private files stop installation.
2. Preparation writes new filenames, preserving original native launchers and
   results: `Start-Atlas-Grok-Execution.ps1`, `Review-Grok-Backtest.ps1` and
   `Pause-Grok-Execution.ps1`, plus `Show-Grok-Latency.ps1`. No GUI, Grok or market-data request is started.
3. Launch the NEW Atlas execution script manually and click START normally.
   Confirm PAPER and the Grok order-review notice. For crypto, start the visible
   crypto module/data and enable its AI entries explicitly. There is no second
   Grok intentions shell to start for equity/crypto execution.
4. Optionally run `Review-Grok-Backtest.ps1 -InputJson <raw-comparison.json>` in
   another shell. SIP replay summaries and screenshots are not this input
   schema. Existing deterministic replay tools continue to produce their own
   research results; they are not automatically imported or promoted here.
5. Use the pause script to revoke new admissions. It leaves protection running.
   A new or extended experiment requires a separate explicit preparation; this
   installer never renews the original trial or refills budgets.

Live trading remains false. `.env` and credentials are not modified. The
`ATLAS_GROK_PAPER_ENTRIES_ENABLED=true` flag is used only with this explicit,
validated execution lease and the shared PAPER controller. The original native
launcher remains available for a separate run after normal shutdown; it does not
activate this Grok pilot.

## Evidence and readiness

The local daily journal records subscribed market events, bar windows, native
assessments and PAPER order/fill events. Crypto records post-model execution
quotes, proposal dispatch outcomes and protection/position evidence. Request
contexts, model answers and attempted-call accounting live in
`grok-request-budget.sqlite3`; no credentials are stored there. The original
entry budget retains reservations from both asset classes. Final daily summaries
include the Grok lease/budget status. Backtest review writes its separate status
and dedicated learning journal.

The explicit experiment raw journal is bounded to 4 GiB / eight million records;
the sampled context capture is 64 MiB / 10,000 records. Queue loss, capacity or
writer failure makes coverage incomplete and blocks new trial admission. No
recorder can reconstruct unsubscribed symbols, missing provider events or
pre-start history. A model request, an accepted proposal, a submitted PAPER order
and an actual simulated fill are distinct events. Only reconciled closed fills
can support realized win-rate/PnL analysis.

Offline checks exercise durable shared call limits, restart/revocation,
unchanged parent budgets, explicit desktop/controller wiring, post-model crypto
freshness and independent protection. They establish implementation behavior,
not online latency, profitable strategies or a successful Windows installation.
Those require the operator's reviewed run and resulting local evidence.

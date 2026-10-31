# Explicit next Grok PAPER window, with shared budgets retained

Use `tools/prepare-grok-execution-trial.ps1 -NextWindow` only for an explicitly
requested continuation after the prior short entry window has expired. Normal
Atlas launches never create or renew a continuation. Close Atlas and old Grok
workers normally before installation; no process is killed automatically.

The installer verifies local configuration and remaining budgets, runs offline
Windows tests, backs up the installed revision and updates by fast-forward. It
then appends a new approval to `execution_windows` in the existing dedicated
Grok budget database and writes a separate `grok-execution-window-<id>.json`.
The original lease remains the immutable budget anchor. The parent experiment
ID, original parent expiry, original native launchers, `.env` and entry ledger
remain unchanged. The approval is not a strategy promotion.

Each new window lasts at most thirty minutes from preparation, ending sooner at
the original parent expiry. An overlapping active window, pause marker, changed
anchor, missing evidence, exhausted budget or expired parent blocks preparation.
No window deletes prior request rows or replenishes either budget:

- Twenty paid attempts across all windows and retained prior intentions,
  including failed requests and the disconnected low-reasoning probe.
- Fifty shared native/crypto entry attempts and $5,000 total entry notional
  across the original parent experiment. Failed reservations still count.
- Existing equity ten-share/$100 position/$1 modeled initial-risk limits and
  crypto $100 reserved-capital/$1 modeled initial-risk limits remain in force.

The new document preserves the original prior-call offset, and all windows read
the same `requests` table and original entry-attempt database. An unregistered
edited/copied lease cannot authorize a new deadline. Removing/changing its
approval or anchor revokes a registered window. A missing shared call ledger
cannot be recreated by a continuation window. Stop/pause markers revoke entry
permission without disabling native protective exits.

The generated unique launcher retains its exact window path. For convenience,
`Start-Atlas-Grok-Next-Window.ps1` points literally to the latest explicitly
prepared launcher; it does not search folders, select old trials or renew a
deadline. The installer never runs it. Start it manually, confirm PAPER and
click START in the visible Atlas GUI. Start crypto's module/data and enable its
AI proposals visibly if that separate crypto test is desired. Do not run the
old intentions-only process concurrently.

This run uses the installed explicit execution suite with one combined equity
review and low reasoning for the documented exact Grok 4.5/4.6/4.7 IDs. Its role
is unchanged: selection of native equity entry candidates and separate bounded
crypto BUY/SELL proposals. Independent whole-equity signal generation and
free-form position management are not added. Current native setup, quote,
account, risk and ownership checks still control submission; a model reply is
not a fill. A historical low-effort response is not proof of live profitability
or reliable sub-two-second response time.

Recording continues with the existing daily journal, request/context ledger and
native fill events. Each window's manifest retains its own document and baseline.
The preparation summary reports remaining calls, entry attempts and notional.
`Show-Grok-Next-Window-Latency.ps1` reports only execution-role attempts reserved
during the approved window. Earlier timeouts and LEARNING research probes are
excluded from that window's timing statistics, while the original all-history
report and budget still include them. The approved-window report works after
its expiry and makes no API calls.
The new approval row is the only budget-schema addition; previous call rows and
original entry-budget bytes are preserved during preparation. Model calls,
market-data requests, GUI startup, orders and subscription purchases are all
zero during installation. Existing positions still need Atlas and its data
running for protective exits after new admissions pause or expire.

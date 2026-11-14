# Equity session and stream observer repair

The prior eight-hour trial could expire overnight while Atlas and the recorder
continued running. Expired policies reject new buys, so RUNNING did not mean
entry authority was active. Old specifications, budgets and ledgers are preserved.

`tools/repair-equity-session.ps1` requires Atlas to be closed, runs regression
checks in an isolated checkout, reads the existing trial without renewing it,
fast-forwards the installed source, and prepares a separate manual account-sized
PAPER trial with an explicit 24-hour entry window. It starts nothing and does not
restart crypto or change the environment file. The clock starts at first manual
launcher execution; closing or restarting Atlas does not reset its expiry.

The full-day option is accepted only for GROK_WARRIOR_ACCOUNT_V6. Default trials
remain eight hours. Normal account risk, quote freshness, stops, and ownership
checks still apply. The shared Warrior budget remains twenty paid calls: at most
eight Director checks paced three hours apart and twelve native reviews. This is
not continuous model review of every signal. Recording duration covers the full
window; existing disk, byte and record limits remain and can end evidence capture.

Morning status explicitly reports entry-window state, expiry, pause markers,
observer input warnings, and Scalper bridge rejections. The GUI notice explicitly
states when the entry window has expired. A fresh window does not itself qualify
a trading signal or promise any orders or profitability.

The observer now recognizes the PAPER stream wrapper. It binds provenance to the
exact snapshot returned on the native assessment thread before queue handoff.
Fresh subscribed quotes retain WEBULL_STREAM provenance and original provider
timestamps. REST fallback retains WEBULL_OPENAPI provenance only when backed by
the native Webull source. A copied or unrelated snapshot is refused. Stream
confirmation is never included in the paired STREAM-versus-REST comparison.
All observer consumers accept the explicit stream timestamp basis. Observer
freshness requirements remain unchanged, including refusing future snapshots.

Expired-trial protection startup now also permits the recorder to attach without
renewing entry authority, preserving diagnostics during position management.
No workstation ledger was altered by the offline tests. Actual protection must
still be inspected after the operator's manual launch.

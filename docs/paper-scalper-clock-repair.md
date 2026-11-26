# Explicit PAPER Scalper clock experiment

The optional `--clock-tolerance` switch is valid only with
`GROK_WARRIOR_ACCOUNT_V6`. It records a sealed `scalper_clock_policy` in a new
experiment specification: at most 250 ms future difference and a five-second
maximum BID/ASK age. Without the switch, all existing profiles and trials keep
zero future tolerance. Provider timestamps, original prices and journal records
are never rewritten. Larger future differences and old books remain rejected.

The same bound applies to Scalper initial policy assessment, one-call REST
confirmation eligibility, and owned-position quote freshness. Stops, targets,
partial exits, matching prices, exposure controls, symbol ownership and native
account sizing rules do not change. It does not add a stream-only order path or
skip canonical confirmation, risk authorization, the PAPER gateway or protection.
The experiment composition continues to require PAPER with live trading disabled.

`prepare-equity-clock-trial.ps1` checks that Windows Atlas/Grok producers are
stopped and tracked files are clean. It creates an isolated review checkout,
runs regression checks and the offline comparison, then backs up the branch and
fast-forwards. It prepares new manual launchers. The new eight-hour clock and
normal twenty-attempt embedded Warrior allowance begin at manual launch. Existing
trial files, expired leases, journals, ledgers and crypto containers are retained.
It makes no API requests and starts no trading process. Preparation/online
credentials and model availability are distinct.

`scalper_clock_replay` accepts bounded original account-profile evidence, binds
it to exact reviewed pre-change source hashes including Windows CRLF, checks each
selected record hash and requires strict baseline policy reproduction before
comparing. Historical configuration construction is pinned to the recorded
profile and matching desktop defaults. Any mismatch refuses preparation. The
selected records cover captured trade windows and recorded confirmations, not
all-day opportunity coverage. The earlier export's full-prefix verification is
reported separately; this replay cannot reverify omitted raw records.

The comparison measures policy labels only. Repeated executable assessments are
not unique opportunities, submitted orders, fills, or expected profits. A timing
repair does not establish a profitable strategy or a win rate. Old REST quotes
must still be resolved through the provider path, rather than retimed or treated
as executable.

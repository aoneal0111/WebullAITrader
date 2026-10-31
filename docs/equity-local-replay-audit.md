# Local replay audit with small output

Use this when the normalized minute history is too large to upload. The original
history, manifest and generated trade report remain on the operator's PC. No
market-data credentials, provider requests, subscriptions, GUI launch or order
services are involved. The optional PowerShell wrapper can fetch GitHub source
and creates/removes its own disposable review worktree; it does not switch,
merge or overwrite the installed Atlas checkout.

`reference.py` independently expresses the three frozen V1 signal definitions
and replay traversal. It does not import or call the original signal, exit,
replay or Costs implementations. It precomputes causal session-prefix VWAP and
fixed decision conditions, searches for completed signals, enters on the next
open, walks each position to its earliest eligible exit, then resumes signal
search after the frozen cooldown. Stops, targets, holding limits, same-session
exits, gap precedence and both-side costs are explicit. The original strategy
rules and production BUY quarantine are unchanged.

The runner shares the Bar schema/validator and exchange calendar with existing
research. This is an independently expressed strategy/replay cross-check, not
independent provider data or broker-execution verification. It checks input
hashes against the original report and manifest and checks the original plan
and source identities. Windows CRLF/LF differences are normalized only when
matching the recorded source bytes. Actual candle-file hashes stay exact.

All six symbols and every policy/window/normal-stress report cell are retained,
including not-applicable cells. Signals, entry/exit timestamps, exit reasons,
stops, prices, net P&L/share and R are compared trade by trade, including missing
or extra trades. Numeric tolerance is 1e-18; timestamps and reasons must match
exactly. A count of 108 comparison cells includes the 24 not-applicable noise-
band cells. A match establishes agreement between implementations on supplied
bars, not that either strategy is profitable or that minute bars prove fills.

The reference also reruns a fixed diagnostic grid: zero costs, 1bp per side plus
$0.0035/share per side, original normal costs and original stress costs. Each
scenario recomputes fills, targets, exits and subsequent trades. All scenarios
are output; there is no selection or promotion. These periods were already
reviewed and remain retrospective. No new untouched holdout is claimed. The
result always has `promotion: NONE` and `execution_authorized: false`.

Output contains hashes, 108 comparison summaries with at most three field-name
examples each, session counts, 36 aggregate diagnostic cells and limitations.
It contains no candle arrays, full trade arrays, raw responses or credentials.
A synthetic end-to-end test verifies ordinary output stays below 100KB. A
summary can be uploaded without sending the large raw datasets.

Run directly from the source checkout:

```powershell
& .\.venv\Scripts\python.exe tools/audit-equity-local-history.py `
    --history 'CAPTURE_DIRECTORY\history' `
    --report 'CAPTURE_DIRECTORY\report.json' `
    --output 'NEW_SMALL_SUMMARY.json'
```

Or run `tools/run-local-equity-audit.ps1 -Repo REPO_PATH -Revision REVIEWED_SHA`.
Its optional `-ResearchRoot` selects a capture explicitly. Otherwise it searches
usual local locations to depth four and selects the most recently modified
folder that contains the report, manifest and all six normalized files. The
Python audit then validates their identity before comparison. If the folder was
moved elsewhere, provide its actual location; no automatic redownload occurs.
The wrapper runs focused mechanical tests before analysis and opens Explorer at
the small output file. A replay mismatch preserves the summary and returns a
failure; it never changes strategy admission or edits the capture.

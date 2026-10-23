# Read-only execution cost evidence

Run `tools/capture-execution-cost-audit.ps1` against the Windows Atlas repository.
It fetches reviewed code into a disposable worktree, tests the stdlib-only audit,
reads a bounded consistent SQLite snapshot of active-campaign PAPER orders and
saves an allowlisted report ZIP on the Desktop. It selects that ZIP in Explorer.
It does not launch Atlas, change the runtime branch, submit orders, copy the raw
database, enable diagnostics, or change strategy/risk settings.

```powershell
$Repo = Join-Path $env:USERPROFILE "WebullAITrader"
git -C $Repo fetch origin research/high-win-rate-comparison
if ($LASTEXITCODE -ne 0) { throw "Fetch failed." }
$Script = Join-Path $env:TEMP "capture-execution-cost-audit.ps1"
git -C $Repo show FETCH_HEAD:tools/capture-execution-cost-audit.ps1 |
    Set-Content -LiteralPath $Script -Encoding UTF8
if ($LASTEXITCODE -ne 0) { throw "Script extraction failed." }
& $Script -Repo $Repo
```

The collector uses `python -S` and `unittest`: pandas, Qt, Webull and pytest are
not required. It fails on missing source files, wrong environment, duplicate or
inconsistent fills, invalid numeric values, or source/result limits. It reads
at most 5,000 orders/32 MB and 10,000 fills under the existing ten-second SQLite
progress deadline. It requires an explicit active campaign; legacy campaign
fallbacks are not inferred. Tests preserve source bytes and close connections
before Windows cleanup.

## What the report can measure

Durable QUICK_SCALPER BUY requests retain the observed bid, the original ask
anchor (`scalp_entry_trigger`), quote decision time and provider-side quote times.
The constructor sets that anchor from `quote.ask`; it is not a replacement-order
limit or a quote observed at the eventual fill. The audit can compare recorded
fills to that **decision** midpoint and ask anchor when the timestamps reconcile.
No second quote acquisition is made by the collector.

Defaults admit quotes at most two seconds old at the decision and fills at most
five seconds after it. Both limits are explicit report fields and configurable
collector arguments. Crossed, future, missing and delayed references remain
visible as excluded observations. Changing these bounds changes the matched
sample, and excluded/delayed fills cannot be assumed cheap or representative.

Signed midpoint differences retain price improvement. The aggregate is total
signed dollar difference divided by matched midpoint notional, in basis points;
it is not an unweighted average across fills. Recorded commissions are reported
for all fills, and matched commission basis points use only matched notional.
Partial quantities must reconcile to the durable filled quantity. Raw order,
fill and campaign IDs, unrelated metadata, credentials and account IDs are omitted.

## What remains unverified

The source is a PAPER ledger. Decision-to-fill differences mix market movement
and execution; they cannot isolate live slippage. Unsupported engines/sides,
including missing SELL quotes, remain unmatched. The report always emits
`costs_verified=false` and `INSUFFICIENT_LIVE_EXECUTION_EVIDENCE`, even when some
references match. This is a successful diagnostic result, not a collector error.

Live cost calibration still needs contemporaneous executable bid/ask with
availability timestamps, matched broker fills and order submission/acknowledgment
times for both sides, representative sessions and verified fee/provenance data.
Paper matches do not supply that evidence. No candidate is promoted and no
cost allowance is lowered automatically from this report.

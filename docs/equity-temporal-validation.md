# Broader equity validation protocol

This follows the c84ca89 equity BUY quarantine. It adds offline research, not an
approved replacement strategy. The release promotion registry remains empty.
No GUI, account lookup, broker order, subscription purchase or Grok activation
is part of this workflow.

## Frozen comparison

`tools/validate-equity-overhaul.py freeze` records fixed dates, symbols, costs,
thresholds, bootstrap seed, plan SHA-256 and source SHA-256 values. Run it before
selecting/capturing the dataset. The run command rejects changed plans or code.
Do not weaken thresholds or tune rules after reading later-period results.
A changed strategy needs a separately named experiment and new unseen evidence.

| Period | Inclusive start | Exclusive end |
| --- | --- | --- |
| Development | 2025-01-02 | 2026-01-01 |
| Validation | 2026-01-01 | 2026-07-01 |
| Retrospective holdout partition | 2026-07-01 | 2026-09-25 |

All dates are exchange trading dates, not UTC calendar splits. These are
retrospective partitions: unseen status is not proven and they are not a
prospective holdout. The last partition ends before the already inspected
September 25–October 22 exploratory sample. A genuinely prospective trial still
needs a separately frozen future period and matched forward PAPER evidence.

The six-symbol liquid universe and three policies are unchanged. Noise-band is
applicable to SPY/QQQ only; the other four cells are explicitly not applicable.
Every other symbol/policy/period/cost cell is retained, including zero trades,
missing symbols and failures. This is not a historical stocks-in-play universe
or evidence that small-cap mover selection works.

Normal assumed costs remain 5bps per side plus $0.0035/share per side; stress is
10bps plus $0.007/share. Neither represents measured Webull execution. Replay
uses completed signals, next-open entry, both-side costs, gap stops, stop-first
ambiguity and session exits. Previous bars warm indicators but cannot initiate
trades in the evaluation period. Calendar boundaries and complete minute
coverage are independently checked, including holidays, early closes and DST.
Missing minutes are excluded rather than interpolated or shifted.

## Research screen, never deployment approval

Each policy must separately pass every period under both cost levels. The fixed
research targets are at least 100 closed trades per period, 30 complete dates
per applicable symbol, at least 98% session coverage per applicable symbol,
50% net winning trades, positive net R expectancy and a positive lower endpoint
of the descriptive 95% date-cluster bootstrap interval. Noise-band also needs
all 14 preceding warmup sessions. Thresholds are screening choices, not proof
that these are optimal settings or that a 50% win rate guarantees profit.

R-based profit factor and expectancy are explicitly not account-dollar returns.
All symbols on the same date are resampled together, including zero-trade dates,
with a fixed seed and 2,000 replicates. This handles within-date clustering but
not every multi-day dependence or multiple-testing concern. The interval is
exploratory, not a guarantee. Symbol cells show ordinary metrics; cluster
intervals are computed on the aggregate policy/period/cost cells.

Even a passing research screen returns `promotion: NONE` and
`execution_authorized: false`. Quote-verified execution costs, shared-capital
portfolio replay and drawdown, point-in-time universe evidence, production
signal-adapter parity, genuinely prospective holdout and matched forward PAPER
execution remain deployment blockers. No claim is made that legacy production
execution has been replicated by this harness.

## Capture and run on Windows

The existing short public normalized dataset format is supported, but cannot
satisfy these longer windows. Missing inputs produce explicit coverage failures;
there is no assertion that the broader backtest has run successfully without
those data.

The optional Alpaca collector requests SIP one-minute bars, raw adjustment and
`asof=-` (no current-symbol remapping). Raw prices preserve actual same-day price
levels; overnight price discontinuities/corporate actions can affect the
noise-band reference and need review. The collector filters to NYSE regular
sessions, follows every page token even after short pages, retains raw response
hashes and normalized-file hashes, and reports every incomplete session.
A 400-page / 4MB-per-page / 400,000-regular-bars-per-symbol bound prevents an
unbounded download. It stops on malformed data, repeated tokens or provider
errors; no permission retry, IEX fallback or subscription purchase occurs.
Partial runs retain a safe manifest and cannot masquerade as complete data.
Do not combine partial captures manually to hide failures.

Official API documentation:
- https://docs.alpaca.markets/us/reference/stockbars
- https://docs.alpaca.markets/us/docs/historical-stock-data-1

SIP entitlement and historical availability must be checked on the operator's
account. IEX is a single-venue feed and is not substituted as consolidated data.
This workflow does not require transferring API credentials into chat or a ZIP.
The collector reads `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` from the local
process environment only; it does not copy `.env`, log credentials or save them
in manifests. Do not paste keys into a chat, command transcript or report.

From the repository, using a new private output directory:

```powershell
$Research = Join-Path ([Environment]::GetFolderPath('Desktop')) ('atlas-temporal-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Research
$Plan = Join-Path $Research 'plan.json'
$Data = Join-Path $Research 'history'
$Report = Join-Path $Research 'report.json'
& .\.venv\Scripts\python.exe tools/validate-equity-overhaul.py freeze --plan $Plan
if ($LASTEXITCODE -ne 0) { throw 'Plan freeze failed.' }
# Only after local data credentials and SIP access have been configured:
& .\.venv\Scripts\python.exe tools/validate-equity-overhaul.py capture-alpaca --plan $Plan --output $Data
if ($LASTEXITCODE -ne 0) { throw 'Capture failed; preserve its manifest.' }
& .\.venv\Scripts\python.exe tools/validate-equity-overhaul.py run --plan $Plan --input $Data --output $Report
if ($LASTEXITCODE -ne 0) { throw 'Validation failed.' }
```

Keep raw data, credentials and result files private. Repository publication
contains only code, synthetic tests and this protocol. Hashes prove byte
reproducibility, not the authenticity of a user-supplied manifest. Software tests
use synthetic data and establish mechanics; they are not profitability evidence.

## Capture timezone repair

The HTTP request and response checks now use the same half-open interval:
midnight in America/New_York on the first requested exchange date, through
midnight on the exclusive end date, converted separately to UTC. This matters
when the two boundaries have different DST offsets. The API's inclusive end is
one second before that exclusive instant; accepted minute timestamps are never
shifted. Extended-hours, weekend and holiday rows within the interval are
excluded from regular-session inputs. Truly out-of-request rows still stop
capture and now record a sanitized timestamp/boundary diagnostic.

The original request started at UTC midnight while checking Eastern dates. A
valid prior-Eastern-evening row at the front of that UTC request could cause
`BAR_OUTSIDE_REQUEST`. A request-filtering synthetic provider reproduces that
failure on the original implementation. The repair is covered by that regression,
strict start/end violations, non-session exclusion and separate winter/summer
boundary offsets. This is a code-level reproduction, not inspection of any
operator's raw failed response.

After updating, freeze a new plan in a new output directory: the collector
source hash changed. Preserve the previous partial directory and diagnostics.
No strategy rules, costs, screening thresholds or equity admission change.

param([Parameter(Mandatory=$true)][string]$PaperDirectory)
# Read existing diagnostics only; candidate identity is reported, not assumed.
$ErrorActionPreference = 'Stop'
$Pointer = Get-Content (Join-Path $PaperDirectory 'recording-session.json') -Raw | ConvertFrom-Json
$Session = [string]$Pointer.session
$Manifest = Get-Content (Join-Path $Session 'research-manifest.json') -Raw | ConvertFrom-Json
$Summary = Get-Content (Join-Path $Session 'research-summary.json') -Raw | ConvertFrom-Json
if (-not $Summary.journal.closed) { throw 'Stopped finalized recording required.' }
$Start = [DateTimeOffset]::Parse($Manifest.started_at)
$End = [DateTimeOffset]::Parse($Summary.journal.updated_at)
$Candidates = @()
$Directory = Join-Path $env:TEMP 'atlas-performance'
foreach ($File in (Get-ChildItem $Directory -Filter '*.json' -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1000)) {
    if ($File.Length -gt 16MB) { continue }
    try { $D = Get-Content -LiteralPath $File.FullName -Raw | ConvertFrom-Json } catch { continue }
    if (-not $D.run_id -or -not $D.entry_conversion -or -not $D.startup) { continue }
    if ($File.BaseName -ne $D.run_id) { continue }
    try { $Captured = [DateTimeOffset]::Parse($D.captured_at) } catch { continue }
    if ($Captured -lt $Start -or $Captured -gt $End.AddMinutes(30)) { continue }
    # Reject tiny installer fixtures; still preserve all matching candidates so
    # no newest-file guess is mistaken for an exact runtime identity.
    if ([long]$D.metrics.market_events_received -lt 1000) { continue }
    $Candidates += [pscustomobject]@{
        diagnostic_path = $File.FullName
        diagnostic_sha256 = (Get-FileHash -LiteralPath $File.FullName -Algorithm SHA256).Hash
        run_id = $D.run_id
        captured_at = $D.captured_at
        process_started_at = $D.process_started_at
        status = $D.status
        market_events_received = $D.metrics.market_events_received
        scalper_assessments = $D.metrics.quick_scalper_assessments
        scalper_orders_submitted = $D.metrics.quick_scalper_orders_submitted
        entry_conversion = $D.entry_conversion
        startup = $D.startup
        shutdown = $D.shutdown
        stream = $D.stream
    }
    if ($Candidates.Count -ge 10) { break }
}
$Out = Join-Path $env:LOCALAPPDATA ('AtlasResearch\warrior-entry-gates-' + [guid]::NewGuid().ToString('N') + '.json')
[pscustomobject]@{
    scope = 'STOPPED_WARRIOR_ENTRY_GATE_CANDIDATES_V1'
    recording_session = $Session
    recording_started_at = $Manifest.started_at
    recording_closed_at = $Summary.journal.updated_at
    recording_entry_diagnostics = $Summary.entry_diagnostics
    candidates = $Candidates
    identity_scope = 'CANDIDATES_ONLY; compare times and recording counters before attribution; never selected solely by newest file'
    orders = 0
    provider_requests = 0
} | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Out -Encoding UTF8
Write-Host "UPLOAD_FILE=$Out"
Write-Host ('RUNTIME_CANDIDATES=' + $Candidates.Count)
explorer.exe "/select,`"$Out`""

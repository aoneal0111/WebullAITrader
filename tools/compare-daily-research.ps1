param(
    [string]$Repo = (Join-Path $env:USERPROFILE 'WebullAITrader'),
    [Parameter(Mandatory=$true)][string]$Session,
    [switch]$Online
)
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path -LiteralPath $Repo).Path
$Session = (Resolve-Path -LiteralPath $Session).Path
$Output = Join-Path $Session ('comparison-' + [guid]::NewGuid().ToString('N'))
$Arguments = @('-m','app.asset_modules.daily_research_compare','--repo',$Repo,'--session',$Session,'--output',$Output)
if ($Online) { $Arguments += '--online' }
Push-Location $Repo
try {
    & (Join-Path $Repo '.venv\Scripts\python.exe') @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Historical comparison failed. No execution was enabled.' }
}
finally { Pop-Location }

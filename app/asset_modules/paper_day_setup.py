"""Prepare local manual launchers only. Does not launch Atlas or make API calls."""
import argparse
import json
from pathlib import Path
import shutil

FLAGS={'WEBULL_TRADING_ENVIRONMENT':'PAPER','LIVE_TRADING_ENABLED':'false',
    'WARRIOR_FORWARD_PAPER_ENABLED':'true','QUICK_SCALPER_ENABLED':'true',
    'ATLAS_PAPER_CONTROLLER_ENABLED':'false','ATLAS_GROK_PAPER_ENTRIES_ENABLED':'false',
    'ATLAS_DAILY_RESEARCH_ENABLED':'true','ATLAS_LEARNING_OUTCOME_COLLECTION_ENABLED':'true'}


def quote(value):return "'"+str(value).replace("'","''")+"'"


def prepare(repo,output):
    repo,output=Path(repo).resolve(),Path(output).resolve()
    if shutil.disk_usage(output.parent).free<6*1024**3:raise ValueError('SIX_GIB_FREE_SPACE_REQUIRED_FOR_DAY_CAPTURE')
    output.mkdir(parents=True,exist_ok=False)
    python=repo/'.venv/Scripts/python.exe'
    profile=dict(FLAGS,ATLAS_EQUITY_PAPER_EXPERIMENT=str(output/'experiment.json'),
        ATLAS_LEARNING_OUTCOME_JOURNAL_PATH=str(output/'learning-outcomes.json'))
    assignments='\n'.join('    '+quote(k)+' = '+quote(v) for k,v in profile.items())
    common="$ErrorActionPreference = 'Stop'\n$TrialEnvironment = @{\n"+assignments+"\n}\n$Before = @{}\nforeach ($Name in $TrialEnvironment.Keys) { $Before[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process'); [Environment]::SetEnvironmentVariable($Name, $TrialEnvironment[$Name], 'Process') }\n"
    finally_block="} finally { foreach ($Name in $Before.Keys) { [Environment]::SetEnvironmentVariable($Name, $Before[$Name], 'Process') }; Pop-Location }\n"
    atlas=common+"Push-Location "+quote(repo)+"\ntry {\n"
    atlas+="    $Running = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -match 'app\\.gui\\.app|bootstrap-observer\\.py' }\n"
    atlas+="    if ($Running) { throw 'Close the existing Atlas normally first. Nothing else was started.' }\n"
    atlas+="    & "+quote(python)+" -m app.equity_overhaul.paper_experiment --create "+quote(output)+"\n"
    atlas+="    if ($LASTEXITCODE -ne 0) { throw 'Experiment expired or invalid. Nothing started.' }\n"
    atlas+="    Write-Host 'EXPERIMENTAL PAPER; 8 HOURS; 50 ENTRY ATTEMPTS; 10 SHARES; $100 POSITION; $1 MODELED INITIAL RISK; GROK ORDERS OFF'\n"
    atlas+="    & "+quote(python)+" -m app.gui.app\n"+finally_block
    grok=common+"Push-Location "+quote(repo)+"\ntry {\n"
    grok+="    & "+quote(python)+" -m app.asset_modules.grok_day_proposals --repo "+quote(repo)+" --trial "+quote(output)+"\n"
    grok+="    if ($LASTEXITCODE -ne 0) { throw 'Grok intentions stopped with a diagnostic. Native PAPER protection is independent.' }\n"+finally_block
    (output/'Start-Atlas-Paper-Trial.ps1').write_text(atlas,encoding='utf-8')
    (output/'Start-Grok-Intentions.ps1').write_text(grok,encoding='utf-8')
    (output/'Pause-New-Entries.ps1').write_text("$null = New-Item -ItemType File -Force -Path "+quote(output/'PAUSE_ENTRIES')+"\nWrite-Host 'New entries paused; working buys expire within at most 60 seconds. Keep Atlas open for protective exits.'\n",encoding='utf-8')
    (output/'Stop-Grok.ps1').write_text("$null = New-Item -ItemType File -Force -Path "+quote(output/'STOP_GROK')+"\n",encoding='utf-8')
    summary=dict(scope='MANUAL_PAPER_DAY_TRIAL_PREPARATION_V1',prepared_only=True,atlas_started=False,requests=0,
        flags=FLAGS,lease_created_on_manual_launch=True,capture='Local learning outcome collection; subscribed market events, bar windows, assessments, paper order/fill events, rejection/freshness diagnostics',
        raw_journal_limit_bytes=4*1024**3,raw_journal_limit_records=8_000_000,max_recording_hours=16,
        context_limits='10000 fresh contexts / 64 MiB; raw journal continues afterward',
        grok='At most twenty paid order-intention calls over eight hours; no order dispatch or simulated fills',
        trial='Current canonical native Warrior/Quick Scalper adapters only; no claim that all public repositories are installed',
        limitations=['No guaranteed entry or profit; existing native execution checks remain active.',
            'All-day means bounded recording; queue loss or disk/capacity failure invalidates complete evidence.',
            'No unobserved symbols, missing quotes, news, or market depth invented.',
            'Grok BUY/EXIT opinions are proposals only, not a counterfactual filled-position backtest.'])
    target=output/'preparation-summary.json';target.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print('TRIAL_DIRECTORY='+str(output));print('MANUAL_ATLAS='+str(output/'Start-Atlas-Paper-Trial.ps1'))
    print('MANUAL_GROK='+str(output/'Start-Grok-Intentions.ps1'));print('UPLOAD_SUMMARY='+str(target))
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();prepare(args.repo,args.output)


if __name__=='__main__':main()

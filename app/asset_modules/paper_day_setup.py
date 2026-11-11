"""Prepare local manual launchers only. Does not launch Atlas or make API calls."""
from app.asset_modules.paper_profiles import GROK_CONTROL_PROFILES, ACCOUNT_SIZING_PROFILE, PAPER_PROFILES
import argparse
import json
import os
from pathlib import Path
import shutil

FLAGS={'WEBULL_TRADING_ENVIRONMENT':'PAPER','LIVE_TRADING_ENABLED':'false',
    'WARRIOR_FORWARD_PAPER_ENABLED':'true','QUICK_SCALPER_ENABLED':'true',
    'PAPER_SYMBOL_AUTHORIZATION_MODE':'DYNAMIC_WARRIOR_AND_QUICK_SCALPER',
    'ATLAS_PAPER_CONTROLLER_ENABLED':'false','ATLAS_GROK_PAPER_ENTRIES_ENABLED':'false',
    'ATLAS_DAILY_RESEARCH_ENABLED':'true','ATLAS_LEARNING_OUTCOME_COLLECTION_ENABLED':'true'}


def quote(value):return "'"+str(value).replace("'","''")+"'"


def prepare(repo,output,*,profile='LEGACY_V1',teams=False,extended_sessions=False,clock_tolerance=False,hours=8):
    if type(hours) is not int or hours not in (8,24) or (hours!=8 and profile!=ACCOUNT_SIZING_PROFILE):
        raise ValueError('EXPLICIT_ACCOUNT_PAPER_DURATION_REQUIRED')
    if type(clock_tolerance) is not bool or clock_tolerance and profile!=ACCOUNT_SIZING_PROFILE:
        raise ValueError('EXPLICIT_ACCOUNT_PAPER_CLOCK_PROFILE_REQUIRED')
    if profile not in PAPER_PROFILES:raise ValueError('UNKNOWN_EXPERIMENT_PROFILE')
    if profile in GROK_CONTROL_PROFILES and teams:raise ValueError('WARRIOR_CONTROL_IS_EMBEDDED_NOT_OBSERVER_TEAMS')
    if type(extended_sessions) is not bool or extended_sessions and profile not in GROK_CONTROL_PROFILES:raise ValueError('EXPLICIT_GROK_PAPER_EXTENDED_SESSIONS_REQUIRED')
    repo,output=Path(repo).resolve(),Path(output).resolve()
    if shutil.disk_usage(output.parent).free<6*1024**3:raise ValueError('SIX_GIB_FREE_SPACE_REQUIRED_FOR_DAY_CAPTURE')
    grok_model=None
    if teams or profile in GROK_CONTROL_PROFILES:
        from dotenv import dotenv_values
        from .grok import configured_client
        client=configured_client({**dotenv_values(repo/'.env'),**os.environ})
        if client is None:raise ValueError('LOCAL_GROK_KEY_AND_MODEL_REQUIRED_NO_NETWORK_PROBE')
        grok_model=client.model
    output.mkdir(parents=True,exist_ok=False)
    python=repo/'.venv/Scripts/python.exe'
    trial_environment=dict(FLAGS,ATLAS_EQUITY_PAPER_EXPERIMENT=str(output/'experiment.json'),
        ATLAS_LEARNING_OUTCOME_JOURNAL_PATH=str(output/'learning-outcomes.json'))
    if profile in GROK_CONTROL_PROFILES:
        trial_environment.update(ATLAS_PAPER_CONTROLLER_ENABLED='true',ATLAS_GROK_EXECUTION_TRIAL='',
            ATLAS_CRYPTO_OBSERVATION_CAPTURE='',ATLAS_CRYPTO_NATIVE_OBSERVER_ENABLED='false')
    if teams:
        trial_environment.update(ATLAS_GROK_EXECUTION_TRIAL='',ATLAS_CRYPTO_OBSERVATION_CAPTURE='',
            ATLAS_CRYPTO_NATIVE_OBSERVER_ENABLED='false')
    assignments='\n'.join('    '+quote(k)+' = '+quote(v) for k,v in trial_environment.items())
    common="$ErrorActionPreference = 'Stop'\n$TrialEnvironment = @{\n"+assignments+"\n}\n$Before = @{}\nforeach ($Name in $TrialEnvironment.Keys) { $Before[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process'); [Environment]::SetEnvironmentVariable($Name, $TrialEnvironment[$Name], 'Process') }\n"
    finally_block="} finally { foreach ($Name in $Before.Keys) { [Environment]::SetEnvironmentVariable($Name, $Before[$Name], 'Process') }; Pop-Location }\n"
    atlas=common+"Push-Location "+quote(repo)+"\ntry {\n"
    atlas+="    $Running = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\\.exe$' -and $_.CommandLine -match 'app\\.gui\\.app|bootstrap-observer\\.py' }\n"
    atlas+="    if ($Running) { throw 'Close the existing Atlas normally first. Nothing else was started.' }\n"
    atlas+="    & "+quote(python)+" -m app.equity_overhaul.paper_experiment --create "+quote(output)+" --profile "+quote(profile)+(" --extended-sessions" if extended_sessions else "")+(" --clock-tolerance" if clock_tolerance else "")+" --hours "+str(hours)+"\n"
    atlas+="    if ($LASTEXITCODE -ne 0) { throw 'Experiment expired or invalid. Nothing started.' }\n"
    if profile in GROK_CONTROL_PROFILES:
        atlas+="    Write-Host 'GROK CONTROLS WARRIOR ONLY; NATIVE CANDIDATE REVIEW + DIRECTED ENTRIES/EXITS; MAX 20 PAID CALLS; SCALPER INDEPENDENT; PROTECTIVE EXITS RETAINED'\n"
    sizing_notice=(f"EXPERIMENTAL PAPER; {hours} HOURS; NATIVE ACCOUNT SIZING; NO TRIAL POSITION OR ENTRY-NOTIONAL OVERRIDES; GROK WARRIOR CONTROL ON" if profile==ACCOUNT_SIZING_PROFILE else "EXPERIMENTAL PAPER; 8 HOURS; 50 ENTRY ATTEMPTS; 10 SHARES; $100 POSITION; $1 MODELED INITIAL RISK; "+("GROK WARRIOR CONTROL ON" if profile in GROK_CONTROL_PROFILES else "GROK ORDERS OFF"))
    atlas+="    Write-Host "+quote(sizing_notice)+"\n"
    if extended_sessions:atlas+="    Write-Host 'PAPER ENTRY SESSIONS: PREMARKET / REGULAR / AFTER_HOURS; EXPLICIT EXPIRY, PAID-REQUEST CAP AND NATIVE ACCOUNT RISK STILL APPLY'\n"
    atlas+="    & "+quote(python)+" -m app.gui.app\n"+finally_block
    grok=common+"Push-Location "+quote(repo)+"\ntry {\n"
    grok+="    & "+quote(python)+" -m app.asset_modules.grok_day_proposals --repo "+quote(repo)+" --trial "+quote(output)+(" --teams" if teams else "")+"\n"
    grok+="    if ($LASTEXITCODE -ne 0) { throw 'Grok intentions stopped with a diagnostic. Native PAPER protection is independent.' }\n"+finally_block
    (output/'Start-Atlas-Paper-Trial.ps1').write_text(atlas,encoding='utf-8')
    if profile not in GROK_CONTROL_PROFILES:
        (output/'Start-Grok-Intentions.ps1').write_text(grok,encoding='utf-8')
    if teams:
        (output/'Start-Grok-Teams.ps1').write_text(grok,encoding='utf-8')
    if teams or profile in GROK_CONTROL_PROFILES:
        (output/'Show-Morning-Status.ps1').write_text(
            "$ErrorActionPreference = 'Stop'\nPush-Location "+quote(repo)+"\ntry {\n    & "+quote(python)+
            " -m app.asset_modules.paper_morning_status --trial "+quote(output)+
            "\n} finally { Pop-Location }\n",encoding='utf-8')
    (output/'Pause-New-Entries.ps1').write_text("$null = New-Item -ItemType File -Force -Path "+quote(output/'PAUSE_ENTRIES')+"\nWrite-Host 'New entries paused; working buys expire within at most 60 seconds. Keep Atlas open for protective exits.'\n",encoding='utf-8')
    (output/'Stop-Grok.ps1').write_text("$null = New-Item -ItemType File -Force -Path "+quote(output/'STOP_GROK')+"\n",encoding='utf-8')
    summary=dict(experiment_profile=profile,scope='MANUAL_PAPER_DAY_TRIAL_PREPARATION_V1',prepared_only=True,atlas_started=False,requests=0,extended_sessions=extended_sessions,
        entry_sessions=['PREMARKET','REGULAR','AFTER_HOURS'] if extended_sessions else ['PREMARKET','REGULAR'],
        flags=FLAGS,lease_created_on_manual_launch=True,capture='Local learning outcome collection; subscribed market events, bar windows, assessments, paper order/fill events, rejection/freshness diagnostics',
        raw_journal_limit_bytes=4*1024**3,raw_journal_limit_records=8_000_000,max_recording_hours=max(16,hours),entry_window_hours=hours,
        context_limits='10000 fresh contexts / 64 MiB; raw journal continues afterward',
        grok='At most twenty paid order-intention calls over eight hours; no order dispatch or simulated fills',
        trial='Current canonical native Warrior/Quick Scalper adapters only; no claim that all public repositories are installed',
        limitations=['No guaranteed entry or profit; existing native execution checks remain active.',
            'All-day means bounded recording; queue loss or disk/capacity failure invalidates complete evidence.',
            'No unobserved symbols, missing quotes, news, or market depth invented.',
            'Grok BUY/EXIT opinions are proposals only, not a counterfactual filled-position backtest.'])
    if teams:
        summary.update(grok='Separate Momentum and Scalper analyst/coordinator teams; independent NEWS/SCOUT desk; observations only',
            grok_model=grok_model,credentials_present=True,credentials_validated_online=False,
            shared_paid_attempt_cap=60,engine_attempt_cap=40,news_scout_attempt_cap=20,per_engine_attempt_cap=20,
            native_path_waits_for_grok=False,crypto_nfi_connected=False,
            latest_context_cache='One atomic bounded context per equity engine; raw journal remains authoritative',
            observations='Hourly samples with at most ten two-stage cases per equity engine; not continuous reviews of all 23 families')
    if profile in GROK_CONTROL_PROFILES:
        summary.update(flags=dict(trial_environment),trial='Warrior Grok native review plus independent directed plans through canonical PAPER execution',grok='Embedded Warrior native reviews and independent plans; maximum twenty shared paid requests',warrior_control='GROK_NATIVE_REVIEW_AND_DIRECTOR',scalper_control='NATIVE',
            shared_warrior_paid_cap=20,director_call_cap=8,native_review_call_cap=12,
            automatic_grok_requests_on_start=True,grok_model=grok_model)
    if profile==ACCOUNT_SIZING_PROFILE:
        summary.update(position_sizing='NATIVE_ACCOUNT_RISK',trial_size_overrides=False,trial_entry_attempt_cap=None,trial_cumulative_notional_cap=None,
            native_risk='Configured account buying power, exposure, quantity/position limits and per-trade risk are enforced at dispatch',
            limitations=summary['limitations']+['Explicit trial expiry and twenty Warrior paid attempts remain; normal account/strategy controls still apply.'])
    summary['scalper_clock_tolerance_ms']=250 if clock_tolerance else 0
    target=output/'preparation-summary.json';target.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print('TRIAL_DIRECTORY='+str(output));print('MANUAL_ATLAS='+str(output/'Start-Atlas-Paper-Trial.ps1'))
    if profile not in GROK_CONTROL_PROFILES:print('MANUAL_GROK='+str(output/('Start-Grok-Teams.ps1' if teams else 'Start-Grok-Intentions.ps1')))
    else:print('GROK_CONTROL=EMBEDDED_WARRIOR_ONLY; NO_SEPARATE_GROK_OBSERVER_NEEDED')
    print('UPLOAD_SUMMARY='+str(target))
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',required=True);p.add_argument('--output',required=True)
    p.add_argument('--profile',choices=PAPER_PROFILES,default='LEGACY_V1')
    p.add_argument('--teams',action='store_true');p.add_argument('--extended-sessions',action='store_true');p.add_argument('--clock-tolerance',action='store_true')
    p.add_argument('--hours',type=int,choices=(8,24),default=8)
    args=p.parse_args();prepare(args.repo,args.output,profile=args.profile,teams=args.teams,extended_sessions=args.extended_sessions,clock_tolerance=args.clock_tolerance,hours=args.hours)


if __name__=='__main__':main()

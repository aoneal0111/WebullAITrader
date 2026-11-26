"""Prepare a separate, manual paper comparison; retain existing workers/wallets."""
import argparse
import json
from pathlib import Path
from uuid import uuid4
from app.strategy_lab.nfi_source_reproduction import IMAGE


def prepare(root,source):
    root=Path(root).resolve();source=Path(source).resolve()
    root.mkdir(parents=True,exist_ok=False);work=root/'paper';work.mkdir()
    service=dict(image=IMAGE,restart='unless-stopped',read_only=True,tmpfs=['/tmp'],
        cap_drop=['ALL'],security_opt=['no-new-privileges:true'],working_dir='/check',
        entrypoint=['python'],environment={'PYTHONPATH':'/check','PYTHONDONTWRITEBYTECODE':'1','HOME':'/tmp'},
        volumes=[dict(type='bind',source=str(source),target='/check',read_only=True),
                 dict(type='bind',source=str(work),target='/work')],
        command=['-m','app.strategy_lab.equity_daily_paper','--work','/work','--entry-experiments'])
    (root/'compose.json').write_text(json.dumps(dict(name='atlas-entry-review-'+uuid4().hex,
        services={'equity-entry':service}),indent=2))
    quote=lambda s:"'"+str(s).replace("'","''")+"'"
    prefix="$ErrorActionPreference = 'Stop'\n$Root = "+quote(root)+"\n$Work = Join-Path $Root 'paper'\n$Compose = Join-Path $Root 'compose.json'\n"
    commands={
        'Start-Equity-Entry-Test.ps1':"if (Test-Path (Join-Path $Work 'STOP')) { throw 'Stopped run retained. Prepare a new comparison.' }\n& docker compose -f $Compose up -d equity-entry\nif ($LASTEXITCODE -ne 0) { throw 'Paper worker start failed.' }\nWrite-Host 'PAPER PUBLIC-PRICE COMPARISON; 3 INDEPENDENT WALLETS; NO BROKER ORDERS; NO GROK'\n",
        'Show-Equity-Entry-Test.ps1':"& docker compose -f $Compose ps\n$Status = Join-Path $Work 'engine-status.json'\nif (Test-Path $Status) { Get-Content -LiteralPath $Status -Raw } else { Write-Host 'WAITING_FOR_FIRST_PUBLIC_PRICE_CYCLE' }\n",
        'Pause-Equity-Entry-Test.ps1':"Set-Content (Join-Path $Work 'PAUSE_NEW_ENTRIES') 'OPERATOR_PAUSE'\nWrite-Host 'New entries paused; keep worker running for exits.'\n",
        'Stop-Equity-Entry-Test.ps1':"Set-Content (Join-Path $Work 'PAUSE_NEW_ENTRIES') 'OPERATOR_PAUSE'\nSet-Content (Join-Path $Work 'STOP') 'OPERATOR_STOP'\n& docker compose -f $Compose stop -t 120 equity-entry\nWrite-Host 'Holdings retained; not flattened. Original workers unchanged.'\n"}
    for name,body in commands.items():(root/name).write_text(prefix+body,encoding='utf-8')
    (root/'comparison-manifest.json').write_text(json.dumps(dict(
        scope='EQUITY_ENTRY_ONLY_FORWARD_COMPARISON_V1',
        cases=['RSI2_SPY_REGIME','CONNORS_RSI2','CONNORS_RSI2_R12_5'],
        varied_parameter='CONNORS_RSI2 entry RSI2 10 -> 12.5',exits_changed=False,
        source_strategies_promoted=False,live_trading_enabled=False,original_workers_modified=False,
        wallet_per_case=10000,stake_per_entry=1000,broker_orders=0,
        limitation='Daily public-price proxy, not Warrior, Scalper or broker execution'),indent=2))
    print('ENTRY_TEST_DIRECTORY='+str(root))
    print('MANUAL_ENTRY_TEST='+str(root/'Start-Equity-Entry-Test.ps1'))
    print('READ_ONLY_ENTRY_STATUS='+str(root/'Show-Equity-Entry-Test.ps1'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--source',required=True)
    a=p.parse_args();prepare(a.root,a.source)

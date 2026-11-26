"""Prepare an isolated manual Bollinger paper research comparison."""
import argparse,json
from pathlib import Path
from uuid import uuid4
from app.strategy_lab.prepare import config as base
from app.strategy_lab.crypto_fleet import write,digest
from app.strategy_lab.nfi_source_reproduction import IMAGE


def prepare(root,fleet,source):
    root,fleet,source=map(lambda p:Path(p).resolve(),(root,fleet,source))
    cfg_original=json.loads((fleet/'e0/config.json').read_text(encoding='utf-8-sig'))
    if cfg_original.get('dry_run') is not True:raise ValueError('PAPER_FLEET_REQUIRED')
    pairs=[p for p in cfg_original['exchange']['pair_whitelist'] if p!='AXS/USD']
    if len(pairs)!=27 or not all(p.endswith('/USD') for p in pairs):raise ValueError('REVIEWED_27_MARKET_UNIVERSE_REQUIRED')
    root.mkdir(parents=True,exist_ok=False);work=root/'paper';work.mkdir();(work/'upstream').mkdir()
    identifier=uuid4().hex;cfg=base(identifier)
    for name in ('timeframe','stoploss','use_custom_stoploss','position_adjustment_enable','use_exit_signal',
                 'exit_profit_only','ignore_roi_if_entry_signal','order_types'):cfg.pop(name,None)
    cfg.update(dry_run_wallet=10000,max_open_trades=6,stake_amount='unlimited',strategy='FleetBollinger',
        user_data_dir='/work',strategy_path='/work/strategies',db_url='sqlite:////work/paper.sqlite3',
        cancel_open_orders_on_exit=False,fee=.004)
    cfg['exchange']['pair_whitelist']=pairs;write(work/'config.json',cfg)
    write(work/'profile.json',dict(scope='NATIVE_CRYPTO_FLEET_PROFILE_V1',key='boll',
        strategy='FleetBollinger',timeframe='4h',pairs=pairs,fleet_id=identifier,source_files={},
        config_sha256=digest(work/'config.json'),compiled=False,live_trading_enabled=False,
        published_performance_applies=False,note='Exploratory 2.5-sigma Bollinger candidate; not a daily-profit guarantee'))
    service=dict(image=IMAGE,container_name='atlas-bollinger-paper-'+identifier,restart='no',read_only=True,
        tmpfs=['/tmp'],cap_drop=['ALL'],security_opt=['no-new-privileges:true'],working_dir='/check',entrypoint=['python'],
        volumes=[dict(type='bind',source=str(source),target='/check',read_only=True),
            dict(type='bind',source=str(work),target='/work'),
            dict(type='bind',source=str(fleet/'shared'),target='/shared')],
        command=['-m','app.strategy_lab.crypto_fleet_runtime','--work','/work','--shared','/shared'])
    write(root/'compose.json',dict(name='atlas-bollinger-'+identifier,services={'boll':service}))
    q=lambda p:"'"+str(p).replace("'","''")+"'"
    prefix="$ErrorActionPreference='Stop'\n$Root="+q(root)+"\n$Compose=Join-Path $Root 'compose.json'\n"
    scripts={
        'Start-Bollinger-Paper.ps1':"& docker compose -f $Compose up -d boll\nif ($LASTEXITCODE -ne 0) { throw 'Paper launch failed.' }\n",
        'Show-Bollinger-Paper.ps1':"& docker compose -f $Compose ps\n$Path=Join-Path $Root 'paper\\engine-status.json'\nif (Test-Path $Path) { Get-Content $Path -Raw } else { Write-Host 'Waiting for first native cycle.' }\n",
        'Pause-Bollinger-Entries.ps1':"Set-Content (Join-Path $Root 'paper\\PAUSE_NEW_ENTRIES') 'OPERATOR_PAUSE'\n",
        'Stop-Bollinger-Paper.ps1':"Set-Content (Join-Path $Root 'paper\\PAUSE_NEW_ENTRIES') 'OPERATOR_PAUSE'\n& docker compose -f $Compose stop -t 120 boll\n"}
    for n,body in scripts.items():(root/n).write_text(prefix+body,encoding='utf-8')
    print('MANUAL_START='+str(root/'Start-Bollinger-Paper.ps1'));print('READ_ONLY_STATUS='+str(root/'Show-Bollinger-Paper.ps1'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--fleet',required=True);p.add_argument('--source',required=True)
    a=p.parse_args();prepare(a.root,a.fleet,a.source)

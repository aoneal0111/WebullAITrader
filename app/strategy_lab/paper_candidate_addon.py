"""Prepare separate paper candidates without touching existing NFI/fleet wallets."""
import argparse,json,shutil
from datetime import datetime,UTC
from pathlib import Path
from uuid import uuid4
from app.strategy_lab.crypto_fleet import MODULE,digest,write
from app.strategy_lab.prepare import config as base_config
from app.strategy_lab.nfi_source_reproduction import IMAGE


def prepare(root,revision):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    key=uuid4().hex
    work=root/'crypto';work.mkdir();(work/'upstream').mkdir();(root/'equity').mkdir();(root/'shared').mkdir();(root/'shared').chmod(0o777)
    config=base_config(key)
    for n in ('timeframe','stoploss','use_custom_stoploss','position_adjustment_enable','use_exit_signal','exit_profit_only','ignore_roi_if_entry_signal','order_types'):config.pop(n,None)
    config.update(dry_run_wallet=10000,max_open_trades=6,stake_amount='unlimited',strategy='FleetROCPullback',
        fee=.008,user_data_dir='/work',strategy_path='/work/strategies',db_url='sqlite:////work/paper.sqlite3',
        cancel_open_orders_on_exit=False,bot_name='Atlas PAPER hourly ROC pullback')
    config['exchange']['pair_whitelist']=[s+'/USD' for s in ('BTC','ETH','SOL','XRP','ADA','DOGE','LINK','LTC','AVAX','DOT','BCH','XLM')]
    write(work/'config.json',config)
    write(work/'profile.json',dict(scope='NATIVE_CRYPTO_FLEET_PROFILE_V1',key='roc',strategy='FleetROCPullback',
        timeframe='1h',pairs=config['exchange']['pair_whitelist'],fleet_id=key,source_files={},
        config_sha256=digest(work/'config.json'),compiled=False,live_trading_enabled=False))
    services={}
    for name in ('crypto','equity'):
        services[name]=dict(image=IMAGE,container_name='atlas-paper-addons-'+key+'-'+name,restart='no',read_only=True,
            tmpfs=['/tmp'],cap_drop=['ALL'],security_opt=['no-new-privileges:true'],working_dir='/check',entrypoint=['python'],
            volumes=[dict(type='bind',source=str(MODULE.parents[1].resolve()),target='/check',read_only=True),
                dict(type='bind',source=str((root/name).resolve()),target='/work'),
                dict(type='bind',source=str((root/'shared').resolve()),target='/shared')],
            command=['-m','app.strategy_lab.'+('crypto_fleet_runtime' if name=='crypto' else 'equity_daily_paper'),'--work','/work']+(['--shared','/shared'] if name=='crypto' else []))
    write(root/'compose.json',dict(name='atlas-paper-addons-'+key,services=services))
    manifest=dict(scope='INDEPENDENT_PAPER_CANDIDATE_ADDON_V1',id=key,revision=revision,live_trading_enabled=False,
        old_workers_changed=False,no_trial_expiry=True,crypto_fee_per_side=.008,
        equity_fee_per_side=.0005,equity_slippage_per_side=.0005,equity_stake_per_entry=1000,
        execution_models=dict(crypto='NATIVE_FREQTRADE_DRY_RUN',equity='PUBLIC_PRICE_PAPER_PROXY'),
        limits_note='No daily attempt quota or expiry. Independent 10000 USD wallets; crypto six native slots; equity source-tested 1000 USD stakes. Fresh data, source signals and available cash required.')
    write(root/'addon.json',manifest);return manifest


def report(root):
    root=Path(root);out={'live_trading_enabled':False,'workers':{}}
    preflight=root/'public-preflight.json'
    if preflight.exists():out['last_start_preflight']=json.loads(preflight.read_text())
    for key in ('crypto','equity'):
        p=root/key/'engine-status.json'
        value=json.loads(p.read_text()) if p.exists() else {'state':'NOT_STARTED'}
        stamp=value.get('updated_at')
        age=(datetime.now(UTC)-datetime.fromisoformat(stamp)).total_seconds() if stamp else None
        value['checkpoint_age_seconds']=age
        value['checkpoint_fresh']=age is not None and 0<=age<=(900 if key=='equity' else 120)
        out['workers'][key]=value
    return out


def public_preflight(root):
    """Read-only provider readiness. One failed provider cannot disable the other."""
    import ccxt
    from app.strategy_lab.equity_daily_paper import fetch,parse
    from app.strategy_lab.crypto_fleet_runtime import verify
    root=Path(root);report=dict(scope='ADDON_PUBLIC_READINESS_V1',checked_at=datetime.now(UTC).isoformat(),
        broker_orders=0,grok_requests=0,workers={})
    try:
        verify(root/'crypto')
        exchange=ccxt.kraken({'enableRateLimit':True,'timeout':20000,'options':{'maxRetriesOnFailure':0}})
        rows=exchange.fetch_ohlcv('BTC/USD','1h',limit=240);now=datetime.now(UTC).timestamp()
        completed=[b for b in rows if b[0]/1000+3600<=now]
        ok=len(completed)>=200 and 0<=now-completed[-1][0]/1000-3600<=7200
        report['workers']['crypto']=dict(ready=ok,completed_rows=len(completed),reason='PUBLIC_CANDLES_READY' if ok else 'WARMUP_OR_STALE_DATA')
    except Exception as exc:report['workers']['crypto']=dict(ready=False,reason=type(exc).__name__+':'+str(exc)[:200])
    try:
        bars,quote=parse(fetch('SPY'),datetime.now(UTC))
        report['workers']['equity']=dict(ready=len(bars)>=200,completed_rows=len(bars),
            reason='WAIT_MARKET_OPEN' if not quote['market_open'] else 'PUBLIC_PRICE_READY',quote=quote)
    except Exception as exc:report['workers']['equity']=dict(ready=False,reason=type(exc).__name__+':'+str(exc)[:200])
    write(root/'public-preflight.json',report);return report


def export(root):
    import sqlite3,zipfile
    root=Path(root);target=root/('review-'+uuid4().hex);target.mkdir()
    write(target/'status.json',report(root));shutil.copyfile(root/'addon.json',target/'addon.json')
    for key,filename in [('equity','equity-paper.sqlite3'),('crypto','paper.sqlite3')]:
        out=target/key;out.mkdir();work=root/key
        if (work/filename).exists():
            db=sqlite3.connect((work/filename).resolve().as_uri()+'?mode=ro',uri=True);dest=sqlite3.connect(out/filename)
            try:db.backup(dest)
            finally:dest.close();db.close()
        for name in ['engine-status.json','profile.json','runtime-check.json','capture-final.json','config.json']:
            if (work/name).exists():shutil.copyfile(work/name,out/name)
    archive=target.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED) as z:
        for path in target.rglob('*'):
            if path.is_file():z.write(path,path.relative_to(target))
    return str(archive)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--revision');p.add_argument('--prepare',action='store_true');p.add_argument('--export',action='store_true');p.add_argument('--public-check',action='store_true');a=p.parse_args()
    if a.prepare:result=prepare(a.root,a.revision)
    elif a.public_check:result=public_preflight(a.root)
    elif a.export:print('UPLOAD_REVIEW='+export(a.root));return
    else:result=report(a.root)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()

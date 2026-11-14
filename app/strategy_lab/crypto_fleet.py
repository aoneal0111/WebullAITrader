"""Prepare and review five explicit, independent native crypto PAPER experiments."""
import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
from urllib.request import urlopen
from uuid import uuid4
import zipfile

from app.strategy_lab.nfi_source_reproduction import IMAGE

MODULE = Path(__file__).parent
PROFILES = {
    'e0': dict(strategy='FleetE0V1E', timeframe='5m', source_class='E0V1E',
               note='Current pinned E0V1E defaults; published hyperopt parameters unbound.'),
    'nasos': dict(strategy='FleetNASOS', timeframe='5m', source_class='NotAnotherSMAOffsetStrategyHOv3',
                  note='Pinned upstream source converted to v3; not the Freqle repaired result.'),
    'bb': dict(strategy='FleetBBRPB', timeframe='5m', source_class='BB_RPB_TSL',
               note='Pinned BB_RPB v3 adaptation; NumPy categorical and hourly-ROC repairs; historical v104 result not reproduced.'),
    'crsi': dict(strategy='FleetCRSI', timeframe='4h',
                 note='BTC/ETH long-only next-price adaptation; no SMA backward fill; 15-bar time exit.'),
    'wave': dict(strategy='FleetWave', timeframe='4h',
                 note='SOL long source trend/volume/ATR rules with BTC context; first-target full exit; no AI or private WFO.'),
}
# Original bytes are retained locally. Only wrappers and source references are
# distributed in Atlas; downloaded code is never allowed to run a repo launcher.
SOURCES = [
    ('e0','TheoBrigitte/freqtrade','c9def379b1036064d592c3a0518a07811f5a9a76','strategies/e0v1e/E0V1E.py','e87c7b96477328e4ffd5d93d2626f435e88b1b6e39478a2249f1fcb8343880cd'),
    ('nasos','neozhou2009/freqtrade-strategies','fcca4013e724aacca72cd0599259466c487a1d04','strategies/NotAnotherSMAOffsetStrategyHOv3/NotAnotherSMAOffsetStrategyHOv3.py','d8c6b3f47d4be7176cf9b3f53952bcaac121ae7a7a62528f3825915fd32a9daa'),
    ('bb','jilv220/BB_RPB_TSL','33f83596e23ca214ef12352802aa798cb986b236','BB_RPB_TSL.py','a99b253c7e6f92560088b24072c8007df0a1f8b3d69a3e75ebc7a87ca6dac32e'),
    ('crsi','MRCVEngineer/CRSI','d826a8e4d55c0d7a993e6f06303fcdb34205d31e','src/strategy.py','0afe50968d02c41f66676fcab654c961f17f5b479b28f3a5afa13b34a4b174d1'),
    ('wave','JudyaiLab/hackathon-trading-agent','b2f92dd9cd8af334f918640fe358424f031d1fb3','strategies.py','daa72c7ea2e542b30b7abc526064fc55b9b24f195c2be193ee6df5e91ae9a760'),
    ('wave','JudyaiLab/hackathon-trading-agent','b2f92dd9cd8af334f918640fe358424f031d1fb3','config.py','fe4376e7997ac5981db744d7c56f5b3a421f2e35f16363ff57214bb5842e5e54'),
    ('wave','JudyaiLab/hackathon-trading-agent','b2f92dd9cd8af334f918640fe358424f031d1fb3','indicators.py','394d24948aa6454bc502c4fa21031753aa41df072c51bd831ffb59c88ccce094'),
]


def digest(path): return sha256(Path(path).read_bytes()).hexdigest()
def write(path, value): Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def fetch_sources(root):
    root=Path(root);root.mkdir(parents=True, exist_ok=True)
    for group, repo, ref, name, expected in SOURCES:
        path=root/group/Path(name).name
        if not path.exists():
            path.parent.mkdir(exist_ok=True)
            with urlopen(f'https://raw.githubusercontent.com/{repo}/{ref}/{name}', timeout=45) as response:
                raw=response.read(2_000_001)
            if len(raw)>2_000_000 or sha256(raw).hexdigest()!=expected:
                raise ValueError('PINNED_FLEET_SOURCE_MISMATCH:'+group)
            path.write_bytes(raw)
        if path.is_symlink() or digest(path)!=expected:
            raise ValueError('PINNED_FLEET_SOURCE_CHANGED:'+group)


def verify_sources(root):
    for group, _, _, name, expected in SOURCES:
        path=Path(root)/group/Path(name).name
        if path.is_symlink() or digest(path)!=expected:
            raise ValueError('PINNED_FLEET_SOURCE_CHANGED:'+group)


def prepare(root, source, *, pairs=None, revision=None, nfi_work=None):
    from app.strategy_lab.prepare import config as base_config
    verify_sources(source)
    pairs=pairs or ['BTC/USD','ETH/USD','SOL/USD','XRP/USD','ADA/USD','LINK/USD','LTC/USD','AVAX/USD']
    import re
    if (not 2<=len(pairs)<=40 or len(pairs)!=len(set(pairs))
            or not all(re.fullmatch('[A-Z0-9]+/USD', p) for p in pairs)):
        raise ValueError('UNIQUE_SOURCE_USD_UNIVERSE_REQUIRED')
    if not {'BTC/USD','ETH/USD','SOL/USD'}<=set(pairs):
        raise ValueError('BTC_ETH_SOL_REQUIRED_FOR_SOURCE_SCOPES')
    root=Path(root);root.mkdir(parents=True, exist_ok=False)
    (root/'shared').mkdir()
    (root/'shared').chmod(0o777)
    fleet_id=uuid4().hex
    manifest=dict(scope='FIVE_INDEPENDENT_CRYPTO_PAPER_EXPERIMENTS_V1', id=fleet_id,
        prepared_at=datetime.now(UTC).isoformat(), revision=revision, image=IMAGE,
        live_trading_enabled=False, grok_enabled=False, nfi_changed=False,
        paper_wallet_per_profile=10000, wallets_shared=False,
        no_trial_time_or_daily_entry_limit=True, fee_per_side_assumption=.004,
        published_win_rates_reproduced=False, profiles={},
        nfi_work=str(Path(nfi_work).resolve()) if nfi_work else None)
    for key, definition in PROFILES.items():
        work=root/key;work.mkdir();(work/'upstream').mkdir()
        for group, _, _, name, _ in SOURCES:
            if group==key: shutil.copyfile(Path(source)/key/Path(name).name,work/'upstream'/Path(name).name)
        config=base_config(fleet_id)
        for name in ('timeframe','stoploss','use_custom_stoploss','position_adjustment_enable',
                     'use_exit_signal','exit_profit_only','ignore_roi_if_entry_signal','order_types'):
            config.pop(name, None)
        selected=(['BTC/USD','ETH/USD'] if key=='crsi' else ['SOL/USD'] if key=='wave' else pairs)
        config.update(dry_run_wallet=10000, max_open_trades=6, stake_amount='unlimited',
            strategy=definition['strategy'], user_data_dir='/work', strategy_path='/work/strategies',
            db_url='sqlite:////work/paper.sqlite3', cancel_open_orders_on_exit=False,
            bot_name='Atlas PAPER '+definition['strategy'])
        config['exchange']['pair_whitelist']=selected
        # Shared budget between all five containers; native CCXT also keeps its
        # own limiter. No extra inference, account API or subscription is used.
        config['exchange']['ccxt_config']['rateLimit']=1100
        config['exchange']['ccxt_async_config']['rateLimit']=1100
        write(work/'config.json', config)
        profile=dict(scope='NATIVE_CRYPTO_FLEET_PROFILE_V1', key=key, **definition,
            pairs=selected, fleet_id=fleet_id, source_files={p.name:digest(p) for p in (work/'upstream').iterdir()},
            config_sha256=digest(work/'config.json'), compiled=False,
            live_trading_enabled=False, published_performance_applies=False)
        write(work/'profile.json',profile)
        manifest['profiles'][key]=definition
    write(root/'fleet.json',manifest)
    services={}
    for key in PROFILES:
        services[key]=dict(image=IMAGE,container_name='atlas-crypto-'+fleet_id+'-'+key,
            restart='no',read_only=True,tmpfs=['/tmp'],cap_drop=['ALL'],
            security_opt=['no-new-privileges:true'],working_dir='/check',entrypoint=['python'],
            volumes=[dict(type='bind',source=str(MODULE.parents[1].resolve()),target='/check',read_only=True),
                     dict(type='bind',source=str((root/key).resolve()),target='/work'),
                     dict(type='bind',source=str((root/'shared').resolve()),target='/shared')],
            command=['-m','app.strategy_lab.crypto_fleet_runtime','--work','/work','--shared','/shared'])
    if nfi_work:
        nfi_id=Path(nfi_work).parent.name.removeprefix('nfi-crypto-paper-')
        if not re.fullmatch('[0-9a-f]{32}',nfi_id):raise ValueError('ORIGINAL_NFI_PAPER_ROOT_REQUIRED')
        services['nfi']={**services['e0'], 'container_name':'atlas-nfi-paper-'+nfi_id,
            'volumes':[dict(type='bind',source=str(MODULE.parents[1].resolve()),target='/check',read_only=True),
                       dict(type='bind',source=str(Path(nfi_work).resolve()),target='/work'),
                       dict(type='bind',source=str((root/'shared').resolve()),target='/shared')],
            'command':['-m','app.strategy_lab.crypto_fleet_runtime','--work','/work','--shared','/shared','--nfi']}
    write(root/'compose.json',dict(name='atlas-crypto-'+fleet_id,services=services))
    return manifest


def summary(root):
    from app.strategy_lab.nfi_forward_status import ledger_status
    from app.strategy_lab.nfi_readiness import runtime_freshness
    root=Path(root);manifest=json.loads((root/'fleet.json').read_text())
    rows={}
    for key in PROFILES:
        work=root/key;row=ledger_status(work, filename='paper.sqlite3')
        engine=json.loads((work/'engine-status.json').read_text()) if (work/'engine-status.json').is_file() else {}
        final=json.loads((work/'capture-final.json').read_text()) if (work/'capture-final.json').is_file() else {}
        freshness=runtime_freshness({'engine-status.json':engine,'capture-final.json':final})
        capture=(final.get('journal',{}) if freshness['state']=='STOPPED' else engine.get('capture',{}))
        rows[key]=dict(**row, runtime_observation=freshness, state=engine.get('state','NOT_STARTED'),
            updated_at=engine.get('updated_at'), fresh_ready_pairs=engine.get('fresh_ready_pairs'),
            source_readiness=engine.get('source_readiness'), capture_error=engine.get('capture_error'),
            writer_alive=capture.get('writer_alive'), evidence_complete=capture.get('evidence_complete'),
            capacity_reached=capture.get('capacity_reached'), note=PROFILES[key]['note'])
    if manifest.get('nfi_work'):
        work=Path(manifest['nfi_work'])
        engine=json.loads((work/'engine-status.json').read_text()) if (work/'engine-status.json').exists() else {}
        final=json.loads((work/'capture-final.json').read_text()) if (work/'capture-final.json').exists() else {}
        rows['nfi']=dict(**ledger_status(work),state=engine.get('state','NOT_STARTED'),
            updated_at=engine.get('updated_at'),source_readiness=engine.get('source_readiness'),
            fresh_ready_pairs=engine.get('fresh_ready_pairs'),pause_new_entries=engine.get('pause_new_entries'),
            capture_error=engine.get('capture_error'),writer_alive=engine.get('capture',{}).get('writer_alive'),
            evidence_complete=engine.get('capture',{}).get('evidence_complete'),
            capacity_reached=engine.get('capture',{}).get('capacity_reached'),
            runtime_observation=runtime_freshness({'engine-status.json':engine,'capture-final.json':final}),
            note='Existing NFI wallet/strategy retained; not reset or merged with experimental wallets.')
    return dict(scope='READ_ONLY_FIVE_CRYPTO_PAPER_STATUS',checked_at=datetime.now(UTC).isoformat(),
        fleet_id=manifest['id'], independent_wallets=True,live_trading_enabled=False,profiles=rows,
        note='Each strategy has its own 10000 USD simulated wallet. Do not sum returns as a shared-capital result.')


def export_review(root):
    root=Path(root);target=root/('review-'+uuid4().hex);target.mkdir()
    write(target/'summary.json',summary(root))
    shutil.copyfile(root/'fleet.json',target/'fleet.json')
    manifest=json.loads((root/'fleet.json').read_text())
    locations={key:(root/key,'paper.sqlite3') for key in PROFILES}
    if manifest.get('nfi_work'):locations['nfi']=(Path(manifest['nfi_work']),'nfi-paper.sqlite3')
    for key,(work,filename) in locations.items():
        out=target/key;out.mkdir()
        ledger=work/filename
        if ledger.exists():
            db=sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
            dest=sqlite3.connect(out/ledger.name)
            try: db.backup(dest)
            finally: dest.close();db.close()
        for name in ('profile.json','runtime-check.json','engine-status.json','capture-final.json','config.json',
                     'paper-manifest.json','resolved-config.json','resolved-universe.json'):
            if (work/name).exists(): shutil.copyfile(work/name,out/name)
        log=work/'native-paper.log'
        if log.exists():
            with log.open('rb') as stream:
                stream.seek(max(0,log.stat().st_size-2*1024**2));(out/'log-tail.txt').write_bytes(stream.read())
    (target/'capture-note.txt').write_text('Original hash-chain recording-* journals remain locally. This ZIP contains consistent native DB snapshots, not a complete journal-chain audit.\n')
    archive=target.with_suffix('.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for path in target.rglob('*'):
            if path.is_file():z.write(path,path.relative_to(target))
    return archive


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    action=p.add_mutually_exclusive_group(required=True)
    action.add_argument('--fetch',action='store_true');action.add_argument('--prepare',action='store_true')
    action.add_argument('--status',action='store_true');action.add_argument('--export',action='store_true')
    p.add_argument('--sources',type=Path);p.add_argument('--revision');p.add_argument('--nfi-work',type=Path)
    a=p.parse_args()
    if a.fetch: fetch_sources(a.root);print('FLEET_SOURCES_VERIFIED; NO_MARKET_DATA_REQUESTS');return
    if a.prepare:
        pairs=None
        if a.nfi_work:
            from app.strategy_lab.nfi_forward_paper import selected_config
            pairs=selected_config(a.nfi_work)['exchange']['pair_whitelist']
        result=prepare(a.root,a.sources,pairs=pairs,revision=a.revision,nfi_work=a.nfi_work)
    elif a.export: print('UPLOAD_REVIEW='+str(export_review(a.root)));return
    else:result=summary(a.root)
    print(json.dumps(result,indent=2,allow_nan=False))


if __name__=='__main__':main()

"""Prepare a pinned native NFI spot PAPER engine; never a real exchange account."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil

try:
    from .nfi_source_reproduction import COMMIT, IMAGE, HASHES, digest, jsonc, merge, write
except ImportError:
    from nfi_source_reproduction import COMMIT, IMAGE, HASHES, digest, jsonc, merge, write

EXTRA_HASHES = {
    'configs/blacklist-kraken.json': '03de5c7b25ae5913c5b3a9a5171d110224cd416261bf263b33f9169de638c53a',
    'configs/pairlist-backtest-static-kraken-spot-usdt.json': '97c82dad82dc8eed740eadd546c1cbfc1963b15bad7544ea9cef092330b68790',
}
SOURCE_FILES = ('NostalgiaForInfinityX8.py', 'LICENSE', 'configs/exampleconfig.json',
                'configs/exampleconfig_secret.json', 'configs/trading_mode-spot.json', *EXTRA_HASHES)
TIMEFRAMES = {'5m':300, '15m':900, '1h':3600, '4h':14400, '1d':86400}
SCOPE = 'PINNED_NFI_KRAKEN_USD_PAPER_V1'


def validate_source(source):
    for name in SOURCE_FILES:
        path=Path(source)/name
        if path.is_symlink() or digest(path)!={**HASHES,**EXTRA_HASHES}[name]:
            raise ValueError('PINNED_NFI_SOURCE_CHANGED:'+name)


def paper_config(source):
    validate_source(source)
    config={}
    for name in SOURCE_FILES[2:]:
        merge(config,jsonc((Path(source)/name).read_text(encoding='utf-8')))
    # The pinned file's name contains USDT but its actual Kraken pairs are USD.
    # Match its actual content, never rename historical Binance USDT quotes.
    pairs=config['exchange']['pair_whitelist']
    if not pairs or any(not p.endswith('/USD') for p in pairs):
        raise ValueError('PINNED_KRAKEN_USD_UNIVERSE_REQUIRED')
    config.update(dry_run=True,dry_run_wallet=10000,stake_currency='USD',
        strategy='NostalgiaForInfinityX8',force_entry_enable=False,
        user_data_dir='/work',strategy_path='/work/strategies',
        db_url='sqlite:////work/nfi-paper.sqlite3',initial_state='running',
        bot_name='Atlas NFI Kraken PAPER',cancel_open_orders_on_exit=False)
    config['api_server']['enabled']=False
    config['telegram']['enabled']=False
    config['exchange']['ccxt_config']={'enableRateLimit':True}
    config['exchange']['ccxt_async_config']={'enableRateLimit':True}
    config['pairlists']=[{'method':'StaticPairList'}]
    validate_config(config)
    return config


def validate_config(config):
    expected={'dry_run':True,'dry_run_wallet':10000,'stake_currency':'USD',
        'strategy':'NostalgiaForInfinityX8','force_entry_enable':False,
        'trading_mode':'spot','stake_amount':'unlimited','max_open_trades':6,
        'timeframe':'5m','db_url':'sqlite:////work/nfi-paper.sqlite3',
        'user_data_dir':'/work','strategy_path':'/work/strategies',
        'cancel_open_orders_on_exit':False,'initial_state':'running'}
    if any(config.get(k)!=v or (type(v) is bool and type(config.get(k)) is not bool)
           for k,v in expected.items()):
        raise ValueError('NATIVE_NFI_PAPER_SETTINGS_REQUIRED')
    if config.get('api_server',{}).get('enabled') is not False or config.get('telegram',{}).get('enabled') is not False:
        raise ValueError('EXTERNAL_CONTROL_DISABLED_REQUIRED')
    exchange=config.get('exchange',{})
    if exchange.get('name')!='kraken':raise ValueError('EXPLICIT_KRAKEN_VENUE_REQUIRED')
    if any(exchange.get(k) for k in ('key','secret','password','walletAddress','privateKey','uid','accountId')):
        raise ValueError('NO_EXCHANGE_CREDENTIALS_ALLOWED')
    allowed_exchange={'name','key','password','secret','walletAddress','privateKey',
                      'ccxt_config','ccxt_async_config','pair_whitelist','pair_blacklist'}
    if set(exchange)-allowed_exchange:raise ValueError('UNREVIEWED_EXCHANGE_OVERRIDE')
    if any(exchange.get(k)!={'enableRateLimit':True} for k in ('ccxt_config','ccxt_async_config')):
        raise ValueError('DEFAULT_PUBLIC_EXCHANGE_CONNECTION_REQUIRED')
    if config.get('pairlists')!=[{'method':'StaticPairList'}]:
        raise ValueError('SEALED_STATIC_UNIVERSE_REQUIRED')
    pairs=exchange.get('pair_whitelist',[])
    if not 1<=len(pairs)<=200 or len(pairs)!=len(set(pairs)) or any(
            not re.fullmatch(r'[A-Z0-9]+/USD',p) for p in pairs):
        raise ValueError('BOUNDED_UNIQUE_USD_SPOT_PAIRS_REQUIRED')
    if any(k in config for k in ('add_config_files','strategy_list','stoploss','minimal_roi',
                                'trailing_stop','position_adjustment_enable')):
        raise ValueError('STRATEGY_OR_CONFIG_OVERRIDE_NOT_ALLOWED')


def prepare(source,work,*,atlas_revision=None):
    if atlas_revision is not None and not re.fullmatch('[0-9a-f]{40}',atlas_revision):
        raise ValueError('EXACT_ATLAS_REVISION_REQUIRED')
    config=paper_config(source)
    work=Path(work);work.mkdir(parents=True,exist_ok=False)
    (work/'strategies').mkdir()
    for name in ('NostalgiaForInfinityX8.py','LICENSE'):
        shutil.copyfile(Path(source)/name,work/'strategies'/name)
    write(work/'config.json',config)
    manifest=dict(scope=SCOPE,source_commit=COMMIT,atlas_revision=atlas_revision,image=IMAGE,exchange='kraken',
        quote_currency='USD',wallet='SEPARATE_SIMULATED_10000_USD',
        source_hashes={n:{**HASHES,**EXTRA_HASHES}[n] for n in SOURCE_FILES},
        prepared_hashes={n:digest(work/n) for n in ('config.json','strategies/NostalgiaForInfinityX8.py','strategies/LICENSE')},
        nfi_owns_entries_exits_and_adjustments=True,live_trading_enabled=False,
        equity_configuration_changed=False,grok_enabled=False,
        historical_reference=dict(closed=36,wins=32,win_rate=32/36,
            venue='BINANCE_USDT',period='2024-01',independently_reproduced_here=False),
        limitations=['Historical 88.9% result is not a Kraken forward result or expected win rate',
            'Native NFI can add to losing positions; its source emergency stop is -99%',
            'Separate simulated wallet and ledger; not allocated from the Atlas equity wallet',
            'No API keys, no real orders, no Grok override of the source strategy'])
    write(work/'paper-manifest.json',manifest)
    return manifest


def verify(work):
    work=Path(work)
    manifest=json.loads((work/'paper-manifest.json').read_text())
    if manifest.get('scope')!=SCOPE or manifest.get('source_commit')!=COMMIT or manifest.get('image')!=IMAGE:
        raise ValueError('PINNED_PAPER_MANIFEST_REQUIRED')
    expected_source={n:{**HASHES,**EXTRA_HASHES}[n] for n in SOURCE_FILES}
    if manifest.get('source_hashes')!=expected_source:
        raise ValueError('PINNED_SOURCE_IDENTITIES_REQUIRED')
    required={'config.json','strategies/NostalgiaForInfinityX8.py','strategies/LICENSE'}
    if set(manifest.get('prepared_hashes',{}))!=required:
        raise ValueError('COMPLETE_PREPARED_IDENTITIES_REQUIRED')
    for name,expected in manifest['prepared_hashes'].items():
        if (work/name).is_symlink() or digest(work/name)!=expected:
            raise ValueError('PREPARED_PAPER_FILES_CHANGED:'+name)
    if digest(work/'strategies/NostalgiaForInfinityX8.py')!=HASHES['NostalgiaForInfinityX8.py']:
        raise ValueError('ORIGINAL_STRATEGY_REQUIRED')
    config=json.loads((work/'config.json').read_text())
    validate_config(config)
    return config,manifest


def selected_config(work):
    base,manifest=verify(work)
    path=Path(work)/'resolved-config.json'
    seal=Path(work)/'resolved-universe.json'
    if not path.exists() or not seal.exists():raise ValueError('PUBLIC_MARKET_PREFLIGHT_REQUIRED')
    context=json.loads(seal.read_text())
    if context.get('config_sha256')!=digest(path) or context.get('base_sha256')!=manifest['prepared_hashes']['config.json']:
        raise ValueError('RESOLVED_UNIVERSE_CHANGED')
    config=json.loads(path.read_text());validate_config(config)
    pairs=config['exchange']['pair_whitelist']
    unchanged=deepcopy(config);unchanged['exchange']['pair_whitelist']=base['exchange']['pair_whitelist']
    if unchanged!=base or not 1<=len(pairs)<=40 or not set(pairs)<=set(base['exchange']['pair_whitelist']):
        raise ValueError('ONLY_PUBLIC_UNIVERSE_SELECTION_ALLOWED')
    return config


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--work',type=Path,required=True)
    p.add_argument('--atlas-revision')
    a=p.parse_args();m=prepare(a.source,a.work,atlas_revision=a.atlas_revision)
    print('NFI_PAPER=PREPARED_ONLY; STRATEGY='+m['source_commit']+'; VENUE=KRAKEN_USD; NO_BOT_STARTED')


if __name__=='__main__':main()

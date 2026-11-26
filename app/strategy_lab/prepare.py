"""Prepare an isolated, pinned Freqtrade dry-run experiment; never starts it."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
from uuid import uuid4

MODULE=Path(__file__).resolve().parent
LOCK=json.loads((MODULE/'source-lock.json').read_text(encoding='utf-8'))


def digest(path):return sha256(Path(path).read_bytes()).hexdigest()


def config(lab_id):
    return {'dry_run':True,'dry_run_wallet':1000,'trading_mode':'spot','margin_mode':'',
        'max_open_trades':2,'stake_currency':'USD','stake_amount':50,'tradable_balance_ratio':.99,
        'timeframe':'5m','strategy':'AtlasNFILab','stoploss':-.02,'use_custom_stoploss':False,
        'position_adjustment_enable':False,'force_entry_enable':False,
        'use_exit_signal':True,'exit_profit_only':False,'ignore_roi_if_entry_signal':True,
        'initial_state':'running','cancel_open_orders_on_exit':True,'fee':.004,
        'unfilledtimeout':{'entry':5,'exit':5,'unit':'minutes'},
        'entry_pricing':{'price_side':'other','use_order_book':True,'order_book_top':1,
                         'price_last_balance':0.0},
        'exit_pricing':{'price_side':'other','use_order_book':True,'order_book_top':1},
        'order_types':{'entry':'limit','exit':'limit','stoploss':'market','stoploss_on_exchange':False},
        'exchange':{'name':'kraken','key':'','secret':'','ccxt_config':{'enableRateLimit':True},
                    'ccxt_async_config':{'enableRateLimit':True},
                    'pair_whitelist':['BTC/USD','ETH/USD'],'pair_blacklist':[]},
        'pairlists':[{'method':'StaticPairList'}],'telegram':{'enabled':False,'token':'','chat_id':''},
        'api_server':{'enabled':False,'listen_ip_address':'127.0.0.1','listen_port':8080,
                      'username':'','password':'','jwt_secret_key':'unused-local-dry-run-api-disabled'},'internals':{'process_throttle_secs':5},
        'db_url':'sqlite:////freqtrade/user_data/lab-trades.sqlite',
        'atlas_lab_id':lab_id,'user_data_dir':'/freqtrade/user_data',
        'strategy_path':'/freqtrade/user_data/strategies'}


def prepare(root,source):
    root,source=Path(root),Path(source)
    # Verify source bytes before generating any runnable files.
    for name,expected in [(LOCK['strategy_file'],LOCK['strategy_sha256']),('LICENSE',LOCK['license_sha256'])]:
        if (source/name).is_symlink() or digest(source/name)!=expected:
            raise ValueError('PINNED_NFI_SOURCE_MISMATCH')
    root.mkdir(parents=True,exist_ok=False)
    user=root/'user_data';strategies=user/'strategies';strategies.mkdir(parents=True)
    lab_id=uuid4().hex
    for name in (LOCK['strategy_file'],'LICENSE'):
        shutil.copyfile(source/name,strategies/name)
    shutil.copyfile(MODULE/'AtlasNFILab.py',strategies/'AtlasNFILab.py')
    shutil.copyfile(MODULE/'runtime_check.py',user/'runtime_check.py')
    cfg=config(lab_id);(user/'config.json').write_text(json.dumps(cfg,indent=2)+'\n',encoding='utf-8')
    control={'lab_id':lab_id,'action':'PAUSE','issued_at':0,'expires_at':0}
    (user/'grok-control.json').write_text(json.dumps(control)+'\n',encoding='utf-8')
    # Explicit image digest; no inherited env_file, secrets, ports, or host sockets.
    compose={'services':{'atlas-nfi-lab':{'image':LOCK['image'],'restart':'no',
        'volumes':['./user_data:/freqtrade/user_data',
                   './user_data/config.json:/freqtrade/user_data/config.json:ro',
                   './user_data/strategies:/freqtrade/user_data/strategies:ro'],
        'command':['trade','--config','/freqtrade/user_data/config.json',
                   '--strategy','AtlasNFILab','--strategy-path','/freqtrade/user_data/strategies']}}}
    (root/'compose.json').write_text(json.dumps(compose,indent=2)+'\n',encoding='utf-8')
    paths=['user_data/runtime_check.py','compose.json','user_data/config.json','user_data/strategies/AtlasNFILab.py',
           'user_data/strategies/'+LOCK['strategy_file'],'user_data/strategies/LICENSE']
    manifest={'scope':'ISOLATED_FREQTRADE_DRY_RUN_LAB_V1','lab_id':lab_id,
        'source_lock':LOCK,'files':{p:digest(root/p) for p in paths},
        'prepared_only':True,'atlas_baseline_modified':False,'orders':0,'grok_requests':0,
        'strategy':'ATLAS_NFI_CAPPED_DRY_RUN_V1','upstream_performance_applies':False}
    (root/'lab-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    prepare(a.output,a.source)
    print('LAB_PREPARED; DRY_RUN_ONLY; ENTRIES_PAUSED; ATLAS_UNCHANGED')
    print('LAB_DIRECTORY='+str(a.output.resolve()))


if __name__=='__main__':main()

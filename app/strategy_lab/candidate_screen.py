"""Pinned-source crypto candidate comparison; disconnected native backtests only."""
import argparse
from copy import deepcopy
from datetime import datetime,UTC
from hashlib import sha256
import json
from pathlib import Path
import shutil
import socket
import sys

if __package__:
    from .history_replay import write,digest
    from .archive_history import PAIRS
    from .prepare import config as lab_config
else:
    from history_replay import write,digest
    from archive_history import PAIRS
    from prepare import config as lab_config

LOCK=json.loads(Path(__file__).with_name('candidate-source-lock.json').read_text(encoding='utf-8'))
EXITS=('SOURCE_EXITS','CAPPED_2PCT')
FEES=(.004,.006)
DEVELOPMENT='20260701-20260901'
VALIDATION='20260901-20261001'


def protocol():
    return {'scope':'FROZEN_CRYPTO_CANDIDATE_PROTOCOL_V1','source_lock':LOCK,
        'development':DEVELOPMENT,'validation':VALIDATION,'pairs':list(PAIRS),
        'exit_variants':list(EXITS),'fees_per_side':list(FEES),'startup_candles':400,
        'virtual_wallet':1000,'stake_amount':50,'max_open_trades':2,'detail_timeframe':'1m',
        'development_gate':{'min_nonforced_closed_trades':50,'min_win_rate':.55,'min_profit_factor':1.2,
            'max_account_drawdown':.05,'net_profit':'POSITIVE_AT_BOTH_FEES'},
        'validation_gate':{'min_nonforced_closed_trades':30,'min_win_rate':.55,'min_profit_factor':1.2,
            'max_account_drawdown':.05,'net_profit':'POSITIVE_AT_BOTH_FEES'},
        'selection':'CAPPED_2PCT only; pass both development cost scenarios and sampled prefix audit; highest stress net, ties class name',
        'validation_policy':'Only development-selected candidate; selection written before validation; never switch using validation',
        'activation_authorized':False,'orders':0,'grok_requests':0}


def verify_source(source):
    source=Path(source).resolve()
    for name,expected in [('LICENSE',LOCK['license_sha256']),*((c['path'],c['sha256']) for c in LOCK['candidates'])]:
        path=source/name
        if path.is_symlink() or not path.resolve().is_relative_to(source) or digest(path)!=expected:
            raise ValueError('PINNED_CANDIDATE_SOURCE_CHANGED')


def verify_history(history):
    history=Path(history).resolve();manifest=json.loads((history/'history-manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('scope')!='FROZEN_KRAKEN_Q3_2026_HISTORY_V1' or manifest.get('status')!='COMPLETE'
        or manifest.get('pairs')!=list(PAIRS) or len(manifest.get('files',{}))!=48):
        raise ValueError('COMPLETE_FROZEN_HISTORY_REQUIRED')
    for name,item in manifest['files'].items():
        path=history/'candles'/name
        if (item.get('pair') not in PAIRS or item.get('timeframe') not in ('1m','5m','15m','1h','4h','1d')
            or name!=item['pair'].replace('/','_')+'-'+item['timeframe']+'.json'
            or path.is_symlink() or not path.resolve().is_relative_to(history) or digest(path)!=item['sha256']):
            raise ValueError('FROZEN_HISTORY_CHANGED')
    path=history/'public-market-metadata.json'
    if path.is_symlink() or digest(path)!=manifest.get('market_metadata_sha256'):
        raise ValueError('FROZEN_MARKET_METADATA_CHANGED')
    return manifest,json.loads(path.read_text(encoding='utf-8'))


def wrapper_text(candidate):
    name=candidate['class']
    return f'''# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
from {Path(candidate['path']).stem} import {name}

class Atlas{name}Screen({name}):
    startup_candle_count=400
    can_short=False
    position_adjustment_enable=False
    use_custom_stoploss=False

    def __init__(self, config):
        if (getattr(config.get('runmode'),'value',None)!='backtest'
                or config.get('dry_run') is not True
                or not (Path(config['user_data_dir'])/'OFFLINE_CANDIDATE_ONLY').is_file()):
            raise ValueError('DISCONNECTED_BACKTEST_ONLY')
        super().__init__(config)
'''


def audit_prefix(strategy,history,timeframe):
    """Sampled prefix invariance, not a complete lookahead certification."""
    import pandas as pd
    checks=[]
    for pair in ('BTC/USD','XRP/USD'):
        path=Path(history)/'candles'/(pair.replace('/','_')+'-'+timeframe+'.json')
        frame=pd.DataFrame(json.loads(path.read_text()),columns=['date','open','high','low','close','volume'])
        frame['date']=pd.to_datetime(frame['date'],unit='ms',utc=True)
        for date in ('2026-07-15','2026-08-15','2026-09-15'):
            count=int((frame['date']<pd.Timestamp(date,tz='UTC')).sum())
            sample=frame.iloc[max(0,count-1500):count+100].reset_index(drop=True)
            cutoff=min(1500,count)
            def calc(data):
                out=strategy.populate_indicators(data.copy(deep=True),{'pair':pair})
                out=strategy.populate_entry_trend(out,{'pair':pair})
                return strategy.populate_exit_trend(out,{'pair':pair})
            full=calc(sample);prefix=calc(sample.iloc[:cutoff])
            try:
                pd.testing.assert_frame_equal(full.iloc[:cutoff].tail(200),prefix.tail(200),
                    check_exact=False,rtol=1e-10,atol=1e-10)
                checks.append({'pair':pair,'cutoff':date,'passed':True})
            except AssertionError:
                checks.append({'pair':pair,'cutoff':date,'passed':False})
    return {'scope':'SAMPLED_RAW_FRAME_PREFIX_INVARIANCE','passed':all(c['passed'] for c in checks),'checks':checks}


def passes(rows,minimum):
    return len(rows)==2 and all(r['nonforced_closed_trades']>=minimum and r['profit_total_abs']>0
        and r['win_rate']>=.55 and r['profit_factor']>=1.2 and r['max_drawdown_account']<=.05 for r in rows)


def select_development(runs,audits):
    choices=[]
    for candidate in LOCK['candidates']:
        name=candidate['class'];rows=[r for r in runs if r['candidate']==name and r['exit_variant']=='CAPPED_2PCT']
        if audits.get(name,{}).get('passed') and passes(rows,50):
            choices.append((next(r['profit_total_abs'] for r in rows if r['fee_per_side']==.006),name))
    return sorted(choices,key=lambda x:(-x[0],x[1]))[0][1] if choices else None


def screen(source,history,output):
    def forbidden(*args,**kwargs):raise RuntimeError('CANDIDATE_SCREEN_NETWORK_DISABLED')
    socket.socket.connect=forbidden;socket.socket.connect_ex=forbidden
    socket.socket.sendto=forbidden;socket.getaddrinfo=forbidden
    import ccxt
    import freqtrade
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import ExchangeResolver
    from freqtrade.optimize.backtesting import Backtesting
    if freqtrade.__version__!='2026.9':raise ValueError('PINNED_FREQTRADE_REQUIRED')
    source,history,output=Path(source).resolve(),Path(history).resolve(),Path(output).resolve()
    verify_source(source);manifest,metadata=verify_history(history)
    output.mkdir(parents=True,exist_ok=False)
    frozen=protocol();write(output/'frozen-protocol.json',frozen)
    report={'scope':'OFFLINE_CRYPTO_REPOSITORY_CANDIDATE_SCREEN_V1','status':'PARTIAL',
        'broker_orders':0,'grok_requests':0,'network_requests':0,'activation_authorized':False,
        'atlas_baseline_modified':False,'strategy_promotion':'NONE','selected_candidate':None,
        'protocol_sha256':digest(output/'frozen-protocol.json'),'history_manifest_sha256':digest(history/'history-manifest.json'),
        'source_lock':LOCK,'runtime':{'freqtrade':freqtrade.__version__,'ccxt':ccxt.__version__},
        'development':[],'validation':[],'prefix_audits':{},
        'limitations':['Retrospective temporal split; source and universe selected after the period',
            'No fresh prospective holdout or live execution verified',
            'Candle fill assumptions; no observed historical spread, queue, or account entitlements',
            'Sampled prefix checks are not comprehensive lookahead or recursive-stability certification',
            'Current frozen public market metadata approximates historical minimums and precision',
            'Fixed costs are assumptions, not verified user fee tiers',
            'SOURCE_EXITS variants may have wide stops and cannot be selected for paper admission',
            'Native continuous simulation does not reproduce Grok, leases, bootstrap duration or campaign loss stop']}
    api=ccxt.kraken({'enableRateLimit':True})
    api.publicGetAssets=lambda params={}:metadata['assets']
    api.publicGetAssetPairs=lambda params={}:metadata['pairs']
    api.load_markets()
    def run(candidate,variant,fee,phase):
        title=candidate['class']+'-'+variant+'-'+str(int(fee*10000))+'-'+phase
        work=output/title;work.mkdir();strategies=work/'strategies';strategies.mkdir()
        shutil.copyfile(source/candidate['path'],strategies/Path(candidate['path']).name)
        shutil.copyfile(source/'LICENSE',strategies/'LICENSE')
        classname='Atlas'+candidate['class']+'Screen'
        (strategies/(classname+'.py')).write_text(wrapper_text(candidate),encoding='utf-8')
        (work/'OFFLINE_CANDIDATE_ONLY').write_text('BACKTEST_ONLY\n')
        cfg=lab_config('a'*32)
        # Preserve source ROI, exit signals and trailing rules. Only CAPPED adds the fixed stop.
        for key in ('timeframe','stoploss','use_exit_signal','exit_profit_only','ignore_roi_if_entry_signal'):
            cfg.pop(key,None)
        cfg.update({'runmode':RunMode.BACKTEST,'strategy':classname,'user_data_dir':work,
            'strategy_path':str(strategies),'datadir':history/'candles','dataformat_ohlcv':'json',
            'dataformat_trades':'jsongz','timerange':DEVELOPMENT if phase=='development' else VALIDATION,
            'timeframe_detail':'1m','cache':'none','export':'none','fee':fee,'db_url':'sqlite://',
            'position_stacking':False})
        cfg['exchange']['pair_whitelist']=list(PAIRS)
        if variant=='CAPPED_2PCT':cfg['stoploss']=-.02
        exchange=ExchangeResolver.load_exchange(cfg,validate=False)
        exchange._markets=deepcopy(api.markets);exchange._api.set_markets(api.markets,api.currencies)
        exchange._api_async.set_markets(api.markets,api.currencies)
        exchange.reload_markets=lambda *args,**kwargs:None
        try:
            bt=Backtesting(cfg,exchange=exchange)
            if candidate['class'] not in report['prefix_audits']:
                report['prefix_audits'][candidate['class']]=audit_prefix(bt.strategylist[0],history,candidate['timeframe'])
            bt.start();result=bt.results['strategy'][classname]
            write(work/'native-result.json',result)
            metrics={'candidate':candidate['class'],'exit_variant':variant,'fee_per_side':fee,
                'total_trades':result['total_trades'],
                'nonforced_closed_trades':sum(t['exit_reason']!='force_exit' for t in result['trades']),
                'wins':result['wins'],'losses':result['losses'],'win_rate':result['winrate'],
                'profit_total_abs':result['profit_total_abs'],'profit_factor':result['profit_factor'],
                'max_drawdown_account':result['max_drawdown_account'],'exit_reasons':result['exit_reason_summary'],
                'monthly_results':result.get('periodic_breakdown',{}).get('month',[]),
                'timeframe':bt.strategy.timeframe,'stoploss':bt.strategy.stoploss,
                'resolved_max_open_trades':bt.strategy.config['max_open_trades'],
                'resolved_stake_amount':bt.strategy.config['stake_amount']}
            report[phase].append(metrics)
            write(output/'candidate-screen-summary.json',report)
            print('CANDIDATE_SCENARIO_FINISHED='+title,flush=True)
        finally:exchange.close()
    try:
        for candidate in LOCK['candidates']:
            for variant in EXITS:
                for fee in FEES:run(candidate,variant,fee,'development')
        chosen=select_development(report['development'],report['prefix_audits'])
        report['selected_candidate']=chosen
        write(output/'development-selection.json',{'selected_candidate':chosen,'selection_rule':frozen['selection'],
            'validation_opened':False,'protocol_sha256':report['protocol_sha256']})
        if chosen:
            candidate=next(c for c in LOCK['candidates'] if c['class']==chosen)
            for fee in FEES:run(candidate,'CAPPED_2PCT',fee,'validation')
            report['retrospective_validation_gate_passed']=passes(report['validation'],30)
            report['qualification']='RESEARCH_SHORTLIST_ONLY' if report['retrospective_validation_gate_passed'] else 'VALIDATION_FAILED'
        else:
            report['qualification']='NO_DEVELOPMENT_QUALIFIER'
            report['validation_skipped']='No candidate met preregistered development conditions'
        report['status']='COMPLETE'
    except Exception as exc:
        report['error_type']=type(exc).__name__;raise
    finally:write(output/'candidate-screen-summary.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--history',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--verify-only',action='store_true')
    args=p.parse_args()
    if args.verify_only:
        verify_source(args.source);verify_history(args.history)
        print('CANDIDATE_SOURCE_AND_HISTORY_VERIFIED; REQUESTS=0; ORDERS=0');return
    r=screen(args.source,args.history,args.output)
    print('CANDIDATE_SCREEN='+r['qualification']);print('UPLOAD_SUMMARY='+str((args.output/'candidate-screen-summary.json').resolve()))


if __name__=='__main__':main()

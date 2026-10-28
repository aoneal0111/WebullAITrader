"""Network-disconnected native Freqtrade backtest of the frozen lab adaptation."""
import argparse
from copy import deepcopy
from datetime import UTC,datetime,timedelta
from hashlib import sha256
import json
from pathlib import Path
import shutil
import socket
import sys


def digest(path):return sha256(Path(path).read_bytes()).hexdigest()
def json_value(value):
    if isinstance(value,datetime):return value.isoformat()
    if isinstance(value,timedelta):return value.total_seconds()
    if hasattr(value,'item'):return value.item()
    raise TypeError('UNSUPPORTED_RESULT_TYPE')

def write(path,data):Path(path).write_text(json.dumps(data,indent=2,allow_nan=False,default=json_value)+'\n',encoding='utf-8')


def replay(lab,history,output):
    def forbidden(*args,**kwargs):raise RuntimeError('OFFLINE_REPLAY_NETWORK_DISABLED')
    socket.socket.connect=forbidden;socket.socket.connect_ex=forbidden
    socket.socket.sendto=forbidden;socket.getaddrinfo=forbidden
    from signal_study import verify_lab,LOCK
    from archive_history import PAIRS
    import ccxt
    import freqtrade
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import ExchangeResolver
    from freqtrade.optimize.backtesting import Backtesting
    if freqtrade.__version__!='2026.9':raise ValueError('PINNED_FREQTRADE_REQUIRED')
    lab,history,output=Path(lab).resolve(),Path(history).resolve(),Path(output).resolve()
    verify_lab(lab)
    manifest=json.loads((history/'history-manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('scope')!='FROZEN_KRAKEN_Q3_2026_HISTORY_V1' or manifest.get('status')!='COMPLETE'
        or manifest.get('pairs')!=list(PAIRS) or len(manifest.get('files',{}))!=48):
        raise ValueError('COMPLETE_FROZEN_HISTORY_REQUIRED')
    for name,item in manifest['files'].items():
        path=history/'candles'/name
        expected=item['pair'].replace('/','_')+'-'+item['timeframe']+'.json'
        if name!=expected or path.is_symlink() or not path.resolve().is_relative_to(history) or digest(path)!=item['sha256']:
            raise ValueError('FROZEN_HISTORY_CHANGED')
    metadata=json.loads((history/'public-market-metadata.json').read_text(encoding='utf-8'))
    if digest(history/'public-market-metadata.json')!=manifest.get('market_metadata_sha256'):
        raise ValueError('FROZEN_MARKET_METADATA_CHANGED')
    output.mkdir(parents=True,exist_ok=False)
    summary={'scope':'OFFLINE_ADAPTED_NFI_Q3_2026_SCREEN_V1','status':'PARTIAL','broker_orders':0,'grok_requests':0,
        'network_requests':0,'atlas_baseline_modified':False,'strategy_promotion':'NONE',
        'history_manifest_sha256':digest(history/'history-manifest.json'),'source_lock':LOCK,
        'runtime':{'freqtrade':freqtrade.__version__,'ccxt':ccxt.__version__},'runs':[],
        'limitations':['Historical simulated fills; no live execution or profitability validation',
            'Current frozen public market limits approximate historical precision/minimums',
            'Native candle simulator fill assumptions; no observed historical spread or queue position',
            'No slippage estimate beyond the explicit fee stress scenario',
            'Source omits no-trade candles; native loader may fill gaps with zero-volume flat candles',
            'Survivorship and pair-selection bias: fixed present-day basket',
            'Lookahead and recursive stability checks NOT_RUN; no promotion permitted',
            'No tuning performed; every scenario must be reported, including losses and no-trade runs',
            'Continuous simulated admission; bootstrap duration, lease expiry, Grok decisions and the controller -20 USD campaign stop are not replayed']}
    try:
        # Fixed baseline/broader universe and fee stress; no result-dependent selection.
        for title,pairs,fee in [('BASELINE',PAIRS[:2],.004),('BASKET',PAIRS,.004),('BASKET_COST_STRESS',PAIRS,.006)]:
            work=output/title;work.mkdir();strategies=work/'strategies';strategies.mkdir()
            for name in (LOCK['strategy_file'],'AtlasNFILab.py','LICENSE'):
                shutil.copyfile(lab/'user_data/strategies'/name,strategies/name)
            shutil.copyfile(Path(__file__).with_name('AtlasNFIHistoricalReplay.py'),strategies/'AtlasNFIHistoricalReplay.py')
            (work/'OFFLINE_REPLAY_ONLY').write_text('NO_TRADING_MODE\n')
            c=json.loads((lab/'user_data/config.json').read_text(encoding='utf-8'))
            c.update({'runmode':RunMode.BACKTEST,'strategy':'AtlasNFIHistoricalReplay','user_data_dir':work,
                'strategy_path':str(strategies),'datadir':history/'candles','dataformat_ohlcv':'json',
                'dataformat_trades':'jsongz','timerange':'20260701-20261001','timeframe_detail':'1m',
                'cache':'none','export':'none','fee':fee,'db_url':'sqlite://','stake_currency':'USD',
                'max_open_trades':2,'stake_amount':50,'dry_run_wallet':1000,'dry_run':True})
            c['exchange']['pair_whitelist']=list(pairs)
            api=ccxt.kraken({'enableRateLimit':True})
            api.publicGetAssets=lambda params={}:metadata['assets']
            api.publicGetAssetPairs=lambda params={}:metadata['pairs']
            api.load_markets()
            exchange=ExchangeResolver.load_exchange(c,validate=False)
            exchange._markets=deepcopy(api.markets)
            exchange._api.set_markets(api.markets,api.currencies)
            exchange._api_async.set_markets(api.markets,api.currencies)
            exchange.reload_markets=lambda *args,**kwargs:None
            try:
                bt=Backtesting(c,exchange=exchange);bt.start()
                result=bt.results['strategy']['AtlasNFIHistoricalReplay']
                write(work/'native-backtest-result.json',result)
                summary['runs'].append({'scenario':title,'pairs':list(pairs),'simulated_fee_per_side':fee,
                    'total_trades':result['total_trades'],'wins':result['wins'],'losses':result['losses'],
                    'draws':result['draws'],'win_rate':result['winrate'],
                    'profit_total_abs':result['profit_total_abs'],'profit_factor':result.get('profit_factor'),
                    'max_drawdown_account':result.get('max_drawdown_account'),
                    'exit_reason_summary':result.get('exit_reason_summary'),
                    'monthly_results':result.get('periodic_breakdown',{}).get('month',[]),
                    'timedout_entry_orders':result.get('timedout_entry_orders'),
                    'rejected_signals':result.get('rejected_signals'),
                    'backtest_start':result.get('backtest_start'),'backtest_end':result.get('backtest_end'),
                    'entry_confirmation_checks':bt.strategy.replay_entry_checks,
                    'native_signal_counts':bt.strategy.replay_signal_counts,
                    'resolved_limits':{'max_open_trades':bt.strategy.config['max_open_trades'],
                        'stake_amount':bt.strategy.config['stake_amount'],'stoploss':bt.strategy.stoploss,
                        'position_adjustment_enable':bt.strategy.position_adjustment_enable}})
                print('HISTORICAL_SCENARIO_FINISHED='+title,flush=True)
            finally:exchange.close()
        summary['status']='COMPLETE'
    except Exception as exc:
        summary['error_type']=type(exc).__name__
        raise
    finally:write(output/'historical-replay-summary.json',summary)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lab',type=Path,required=True);p.add_argument('--history',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();replay(a.lab,a.history,a.output)
    print('UPLOAD_SUMMARY='+str((a.output/'historical-replay-summary.json').resolve()))


if __name__=='__main__':main()

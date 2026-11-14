"""Disconnected Freqtrade comparison on sealed current Kraken USD candles."""
import argparse
import ast
from copy import deepcopy
from hashlib import sha256
import json
import inspect
import logging
from pathlib import Path
import shutil
from urllib.request import urlopen
import zipfile
import numpy as np
import pandas as pd
from app.strategy_lab.current_income_grid import frame_from_raw
from app.strategy_lab.crypto_parameter_checks import CallbackAudit
from app.strategy_lab.crypto_parameter_lab import score
from app.strategy_lab.daily_income_lab import Spec,features,pack,simulate,metrics,write
from app.strategy_lab.nfi_runtime_preflight import disconnected
from app.strategy_lab.prepare import config as base_config


def trace_screen(packed,spec,capital,fee):
    """Instrument our frozen Python wallet with events; verify unchanged outputs.

    Only this repository's own engine AST is compiled. No provider text, strategy
    download, user input or generated expression is evaluated as code.
    """
    from app.strategy_lab.daily_income_lab import engine
    tree=ast.parse(inspect.getsource(engine.py_func))
    function=tree.body[0];function.decorator_list=[];function.name='traced_engine'
    function.body.insert(0,ast.parse('trace=[]').body[0])
    class Trace(ast.NodeTransformer):
        def visit_AugAssign(self,node):
            if isinstance(node.target,ast.Name) and node.target.id=='entries':
                event="trace.append(dict(action='BUY',bar=t,symbol=j,quantity=qty[j],entry_cost=cost[j],price=px,signal_atr=pending_atr[j]))"
                return [node,*ast.parse(event).body]
            return node
        def visit_Assign(self,node):
            target=node.targets[0]
            if (isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name)
                and target.value.id=='qty' and isinstance(node.value,ast.Constant) and node.value.value==0):
                event="trace.append(dict(action='SELL',bar=t,symbol=j,quantity=qty[j],entry_cost=cost[j],price=proceeds/(qty[j]*(1-fee)),net_pnl=pnl))"
                return [*ast.parse(event).body,node]
            return node
        def visit_Return(self,node):
            if isinstance(node.value,ast.Tuple):node.value.elts.append(ast.Name(id='trace',ctx=ast.Load()))
            return node
    tree=Trace().visit(tree);ast.fix_missing_locations(tree)
    namespace={'np':np};exec(compile(tree,'<owned-wallet-event-trace>','exec'),namespace)
    args=(*packed,capital,fee,0.,6,spec.stop_atr,spec.target_atr,spec.hold_hours//4)
    traced=namespace['traced_engine'](*args)
    original=engine(*args)
    for a,b in zip(original,traced[:4],strict=True):np.testing.assert_allclose(a,b,rtol=0.,atol=1e-8)
    return traced[-1]


def compare_trades(root):
    root=Path(root);plan=json.loads((root/'plan.json').read_text());frames={}
    for item in plan['data']:
        f=features(pd.read_feather(root/'data'/(item['symbol']+'_USD-4h.feather')))
        f['eligible']=f.date.diff().eq(pd.Timedelta(hours=4)).rolling(200,min_periods=200).sum().eq(200)
        frames[item['symbol']]=f
    spec=Spec('EMA_CROSS',50,3.,8.,168)
    packed=pack(frames,{'current':['2026-08-15','2026-11-08']},'4h','current',spec)
    dates=pd.date_range('2026-08-15','2026-11-08',freq='4h',inclusive='left',tz='UTC')
    symbols=sorted(frames);trace=trace_screen(packed,spec,10000.,.004)
    open_by_pair={};screen=[]
    for row in trace:
        pair=symbols[row['symbol']]+'/USD';at=dates[row['bar']].isoformat()
        if row['action']=='BUY':open_by_pair[pair]=dict(pair=pair,open_date=at,**row)
        else:screen.append(dict(entry=open_by_pair.pop(pair),exit=dict(close_date=at,**row)))
    archive=next((root/'native-10000.0-0.004').glob('*.zip'))
    with zipfile.ZipFile(archive) as z:
        data=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json') and not n.endswith(('.meta.json','_config.json')))))
    strategy=('AtlasIncomeEMAFullHistoryCausal' if 'AtlasIncomeEMAFullHistoryCausal' in plan['strategies']
              else 'AtlasIncomeEMAFullHistory' if 'AtlasIncomeEMAFullHistory' in plan['strategies'] else 'AtlasIncomeEMA')
    stats=data['strategy'][strategy];native=stats['trades']
    index={(t['pair'],pd.Timestamp(t['open_date']).isoformat()):t for t in native}
    pairs=[]
    for row in screen:
        e,x=row['entry'],row['exit'];n=index.get((e['pair'],e['open_date']))
        pairs.append(dict(pair=e['pair'],screen_entry=e,screen_exit=x,native=n,
            same_entry_time=n is not None,same_close_time=n is not None and pd.Timestamp(n['close_date']).isoformat()==x['close_date'],
            screen_win=bool(x['net_pnl']>0),native_win=n['profit_abs']>0 if n else None))
    report=dict(scope='NATIVE_SCREEN_EMA_TRADE_COMPARISON_V1',native_strategy=strategy,screen_closed=len(screen),native_closed=len(native),
        exact_wallet_trace_parity=True,pairs=pairs,
        unmatched_native=[n for n in native if not any(e['entry']['pair']==n['pair'] and e['entry']['open_date']==pd.Timestamp(n['open_date']).isoformat() for e in screen)],
        native_drawdown_fields={k:v for k,v in stats.items() if 'drawdown' in k},orders=0)
    write(root/'trade-comparison.json',report)
    return report


def prepare(root,capture,grid,reuse_catalog=None):
    root,capture,grid=Path(root),Path(capture),Path(grid)
    root.mkdir(exist_ok=False,parents=True)
    meta=json.loads((grid/'capture-4h.json').read_text())
    server=int(pd.Timestamp(meta['anchor_server_time']).timestamp())
    ready=set(json.loads((grid/'review.json').read_text())['timeframes']['4h']['symbols'])
    (root/'data').mkdir();files=[];earliest=None
    for item in meta['rows']:
        if item['symbol'] not in ready:continue
        raw=(grid/'raw-4h'/(item['symbol']+'.json')).read_bytes()
        if sha256(raw).hexdigest()!=item['raw_sha256']:raise ValueError('SEALED_PUBLIC_CANDLES_CHANGED')
        f=frame_from_raw(raw,server,4);name=item['symbol']+'_USD-4h.feather'
        earliest=f.date.min() if earliest is None else min(earliest,f.date.min())
        f.to_feather(root/'data'/name)
        files.append(dict(symbol=item['symbol'],raw_sha256=item['raw_sha256'],
                          feather_sha256=sha256((root/'data'/name).read_bytes()).hexdigest()))
    # One public, read-only catalog response; all backtests below disconnect sockets.
    url='https://api.kraken.com/0/public/AssetPairs'
    if reuse_catalog:raw=Path(reuse_catalog).read_bytes()
    else:
        with urlopen(url,timeout=30) as response:raw=response.read(8*1024*1024+1)
    if len(raw)>8*1024*1024 or json.loads(raw).get('error'):raise ValueError('BOUNDED_VALID_MARKET_CATALOG_REQUIRED')
    (root/'kraken-asset-pairs.json').write_bytes(raw)
    folder=root/'strategies';folder.mkdir()
    source=Path(__file__).parent/'AtlasIncomeEMA.py';shutil.copyfile(source,folder/source.name)
    delayed="from AtlasIncomeEMA import AtlasIncomeEMA\nclass AtlasIncomeEMADelayed(AtlasIncomeEMA):\n    ENTRY_DELAY_BARS=1\n"
    (folder/'AtlasIncomeEMADelayed.py').write_text(delayed)
    warmup=int((pd.Timestamp('2026-08-15',tz='UTC')-earliest).total_seconds()/14400)
    if not 200<=warmup<=710:raise ValueError('REAL_BOUNDED_PREPERIOD_HISTORY_REQUIRED')
    (folder/'AtlasIncomeEMAFullHistory.py').write_text(
        'from AtlasIncomeEMA import AtlasIncomeEMA\nclass AtlasIncomeEMAFullHistory(AtlasIncomeEMA):\n    startup_candle_count='+str(warmup)+'\n')
    (folder/'AtlasIncomeEMAFullHistoryDelayed.py').write_text(
        'from AtlasIncomeEMAFullHistory import AtlasIncomeEMAFullHistory\nclass AtlasIncomeEMAFullHistoryDelayed(AtlasIncomeEMAFullHistory):\n    ENTRY_DELAY_BARS=1\n')
    (folder/'AtlasIncomeEMAFullHistoryCausal.py').write_text(
        'from AtlasIncomeEMA import AtlasIncomeEMACausal\nclass AtlasIncomeEMAFullHistoryCausal(AtlasIncomeEMACausal):\n    startup_candle_count='+str(warmup)+'\n')
    (folder/'AtlasIncomeEMAFullHistoryCausalDelayed.py').write_text(
        'from AtlasIncomeEMAFullHistoryCausal import AtlasIncomeEMAFullHistoryCausal\nclass AtlasIncomeEMAFullHistoryCausalDelayed(AtlasIncomeEMAFullHistoryCausal):\n    ENTRY_DELAY_BARS=1\n')
    plan=dict(scope='NATIVE_CURRENT_EMA_REVIEW_V1',period=['20260815','20261108'],
        strategies=['AtlasIncomeEMAFullHistory','AtlasIncomeEMAFullHistoryDelayed',
                    'AtlasIncomeEMAFullHistoryCausal','AtlasIncomeEMAFullHistoryCausalDelayed'],fees_per_side=[.004,.008],
        capitals=[10000.,100000.],pairs=[x['symbol']+'/USD' for x in files],
        data=files,catalog_sha256=sha256(raw).hexdigest(),catalog_source=url,
        strategy_sha256=sha256(source.read_bytes()).hexdigest(),
        source_chosen_after_current_screen=True,qualification_eligible=False,orders=0,
        public_requests=0 if reuse_catalog else 1,network_requests_during_backtests=0,
        actual_preperiod_warmup_bars=warmup,excluded_unready_symbols=sorted({i['symbol'] for i in meta['rows']}-ready),
        strategy_files_sha256={f.name:sha256(f.read_bytes()).hexdigest() for f in folder.glob('*.py')},
        limitations=['Native OHLC simulation, not real orders, quote verification or fills',
          'Catalog is current at native test time, not verified August execution rules',
          'Screen/native comparison uses zero explicit slippage to isolate engine behavior',
          'Separate original screening numbers include 0.05 percent per-side slippage',
          'Delayed variant means four-hour late entry, not an estimate of Grok latency',
          'Native fees, precision, callback ordering and wallet sizing can differ from screening'])
    write(root/'plan.json',plan)
    return plan


def exchange_from_catalog(config,data):
    import ccxt
    from freqtrade.resolvers import ExchangeResolver
    parser=ccxt.kraken();parser.publicGetAssetPairs=lambda params={}:data
    parser.set_markets(parser.fetch_markets())
    missing=set(config['exchange']['pair_whitelist'])-parser.markets.keys()
    if missing:raise ValueError('CONFIGURED_MARKETS_REQUIRE_REAL_CATALOG:'+','.join(sorted(missing)))
    ex=ExchangeResolver.load_exchange(config,validate=False)
    try:
        ex._markets=deepcopy(parser.markets)
        ex._api.set_markets(parser.markets,parser.currencies)
        ex._api_async.set_markets(parser.markets,parser.currencies)
        ex.reload_markets=lambda *a,**k:None
        ex.validate_config(config)
    except Exception:
        ex.close();raise
    return ex


def marked_metrics(root,stats,initial):
    """Bar-close exit-cost marks from native fills, not closed-only drawdown."""
    dates=pd.date_range('2026-08-15','2026-11-08',freq='4h',inclusive='left',tz='UTC')
    frames={p.name.split('_')[0]+'/USD':pd.read_feather(p).set_index('date') for p in (Path(root)/'data').glob('*.feather')}
    trades=stats['trades'];cash=float(initial);active={};marks={};events={}
    for i,t in enumerate(trades):
        events.setdefault(pd.Timestamp(t['open_date']),[]).append(('BUY',i))
        events.setdefault(pd.Timestamp(t['close_date']),[]).append(('SELL',i))
        if len(t['orders'])!=2:raise ValueError('FULL_SINGLE_ENTRY_EXIT_TRADES_REQUIRED_FOR_MARKING')
    curve=[];entry_counts=np.zeros(85);realized=np.zeros(85)
    for at in dates:
        day=int((at-dates[0]).total_seconds()/86400)
        for action,i in sorted(events.get(at,[]),key=lambda x:x[0]!='SELL'):
            t=trades[i]
            if action=='SELL':
                if i not in active:raise ValueError('NATIVE_EXIT_PRECEDES_ENTRY_IN_MARKING')
                cash+=t['amount']*t['close_rate']*(1-t['fee_close']);del active[i]
                realized[day]+=t['profit_abs']
            else:
                cash-=t['amount']*t['open_rate']*(1+t['fee_open']);active[i]=t;entry_counts[day]+=1
        for pair,f in frames.items():
            if at in f.index:marks[pair]=float(f.loc[at,'close'])
        equity=cash+sum(t['amount']*marks.get(t['pair'],t['open_rate'])*(1-t['fee_close']) for t in active.values())
        curve.append(equity)
    if active:raise ValueError('ALL_NATIVE_TRADES_REQUIRE_TERMINAL_CLOSE')
    if abs(cash-initial-stats['profit_total_abs'])>1e-4:raise ValueError('NATIVE_MARKED_LEDGER_MISMATCH')
    daily=np.asarray(curve).reshape(85,6)[:,-1]
    changes=np.diff(np.r_[initial,daily])-2.
    peak=np.maximum.accumulate(np.r_[initial,curve])[1:]
    return dict(scope='NATIVE_FILL_DERIVED_BAR_CLOSE_MARKS_V1',
        maximum_marked_drawdown=float(np.max((peak-np.asarray(curve))/peak)),
        worst_net_marked_day=float(changes.min()),median_net_marked_day=float(np.median(changes)),
        days_at_or_above_100=int((changes>=100).sum()),negative_net_marked_days=int((changes<0).sum()),
        positive_net_marked_days=int((changes>0).sum()),
        realized_days_at_or_above_100=int((realized-2>=100).sum()),calendar_days=85,
        daily_equity=daily.tolist(),daily_realized=realized.tolist(),daily_entries=entry_counts.tolist(),
        final_cash=cash,ledger_error=abs(cash-initial-stats['profit_total_abs']),
        intrabar_path_verified=False)


def run(root):
    root=Path(root).resolve();plan=json.loads((root/'plan.json').read_text())
    source=root/'strategies/AtlasIncomeEMA.py'
    if sha256(source.read_bytes()).hexdigest()!=plan['strategy_sha256']:raise ValueError('FROZEN_STRATEGY_CHANGED')
    for name,digest in plan.get('strategy_files_sha256',{}).items():
        if sha256((root/'strategies'/name).read_bytes()).hexdigest()!=digest:raise ValueError('FROZEN_VARIANT_CHANGED')
    catalog=root/'kraken-asset-pairs.json'
    if sha256(catalog.read_bytes()).hexdigest()!=plan['catalog_sha256']:raise ValueError('FROZEN_CATALOG_CHANGED')
    frames={}
    for item in plan['data']:
        path=root/'data'/(item['symbol']+'_USD-4h.feather')
        if sha256(path.read_bytes()).hexdigest()!=item['feather_sha256']:raise ValueError('FROZEN_CANDLES_CHANGED')
        f=features(pd.read_feather(path));f['eligible']=f.date.diff().eq(pd.Timedelta(hours=4)).rolling(200,min_periods=200).sum().eq(200)
        frames[item['symbol']]=f
    spec=Spec('EMA_CROSS',50,3.,8.,168)
    packed=pack(frames,{'current':['2026-08-15','2026-11-08']},'4h','current',spec)
    results=[]
    for capital in plan['capitals']:
      for fee in plan['fees_per_side']:
        work=root/('native-'+str(capital)+'-'+str(fee));work.mkdir(exist_ok=False)
        cfg=base_config('offline-native-income')
        for key in ('stoploss','use_custom_stoploss','position_adjustment_enable','use_exit_signal',
                    'exit_profit_only','ignore_roi_if_entry_signal','order_types'):cfg.pop(key,None)
        cfg.update(timeframe='4h',stake_currency='USD',dry_run_wallet=capital,stake_amount='unlimited',
                   max_open_trades=6,fee=fee,user_data_dir=str(work),db_url='sqlite://',
                   cancel_open_orders_on_exit=False)
        cfg['exchange'].update(name='kraken',pair_whitelist=plan['pairs'])
        cfg['order_types']=dict(entry='market',exit='market',stoploss='market',stoploss_on_exchange=False)
        write(work/'config.json',cfg)
        audit=CallbackAudit();logger=logging.getLogger('freqtrade.strategy.strategy_wrapper');ex=None
        try:
          with disconnected():
            from freqtrade.commands.arguments import Arguments
            from freqtrade.configuration import Configuration
            from freqtrade.configuration.config_validation import validate_config_schema
            from freqtrade.enums import RunMode
            from freqtrade.optimize.backtesting import Backtesting
            argv=['backtesting','--config',str(work/'config.json'),'--strategy-list',*plan['strategies'],
                 '--strategy-path',str(root/'strategies'),'--user-data-dir',str(work),
                 '--datadir',str(root/'data'),'--timerange','20260815-20261108',
                 '--cache','none','--export','trades','--export-directory',str(work/'results'),
                 '--log-file',str(work/'native-backtest.log')]
            config=Configuration(Arguments(argv).get_parsed_arg(),RunMode.BACKTEST).get_config()
            validate_config_schema(config,preliminary=True)
            ex=exchange_from_catalog(config,json.loads(catalog.read_text()))
            bt=Backtesting(config,exchange=ex)
            logger.addHandler(audit)
            try:bt.start()
            finally:logger.removeHandler(audit)
            write(work/'callback-audit.json',audit.report())
            if audit.count:raise ValueError('NATIVE_CALLBACK_ERRORS_INVALIDATE_RESULT')
            signal_dates={s.__class__.__name__:getattr(s,'entry_signal_dates',{}) for s in bt.strategylist}
            write(work/'entry-signals.json',signal_dates)
            write(work/'causal-slot-rejections.json',{s.__class__.__name__:getattr(s,'causal_slot_rejections',0) for s in bt.strategylist})
          archives=list(work.glob('*.zip'))
          if len(archives)!=1:raise ValueError('ONE_NATIVE_ARCHIVE_REQUIRED')
          with zipfile.ZipFile(archives[0]) as z:
            raw=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json') and not n.endswith(('.meta.json','_config.json')))))
          screen=metrics(simulate(packed,spec,'4h',fee,capital,slip=0.),capital,2.)
          for name,stats in raw['strategy'].items():
            native_score={k.replace('_usdt','_usd'):v for k,v in score(stats).items()}
            row=dict(strategy=name,capital=capital,fee_per_side=fee,**native_score,
                     callback_audit=audit.report(),network_requests=0,orders=0,
                     native_net_after_overhead=stats['profit_total_abs']-170.,
                     native_average_after_overhead=(stats['profit_total_abs']-170.)/85.,
                     screening_zero_slippage=screen)
            results.append(row)
            print('NATIVE_INCOME_RESULT='+json.dumps(row),flush=True)
          write(root/'native-scores.json',dict(rows=results,qualification_eligible=False,orders=0))
        finally:
          if ex is not None:ex.close()
          write(work/'callback-audit.json',audit.report())
    return results


def augment_marks(root):
    root=Path(root);result=json.loads((root/'native-scores.json').read_text())
    # Older score() helper names used USDT; this catalog and all trades are USD.
    result['rows']=[{k.replace('_usdt','_usd'):v for k,v in row.items()} for row in result['rows']]
    for row in result['rows']:
        work=root/('native-'+str(row['capital'])+'-'+str(row['fee_per_side']))
        archive=next(work.glob('*.zip'))
        with zipfile.ZipFile(archive) as z:
            data=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json') and not n.endswith(('.meta.json','_config.json')))))
        row['marked_risk']=marked_metrics(root,data['strategy'][row['strategy']],row['capital'])
    write(root/'native-marked-review.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--capture',type=Path);p.add_argument('--grid',type=Path);p.add_argument('--prepare',action='store_true')
    p.add_argument('--reuse-catalog',type=Path)
    p.add_argument('--compare-only',action='store_true')
    p.add_argument('--marks-only',action='store_true')
    a=p.parse_args()
    if a.prepare:prepare(a.root,a.capture,a.grid,a.reuse_catalog)
    if a.compare_only:compare_trades(a.root)
    elif a.marks_only:augment_marks(a.root)
    else:run(a.root);compare_trades(a.root);augment_marks(a.root)


if __name__=='__main__':main()

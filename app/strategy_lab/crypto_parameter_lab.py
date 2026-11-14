"""Frozen source-derived crypto risk/entry experiments, disconnected native replay.

Research only: Binance candles and post-period recorded metadata do not qualify
Kraken execution. No running workers, configurations, wallets or orders change.
"""
import argparse
from copy import deepcopy
import json
import logging
from pathlib import Path
import shutil
import zipfile
from app.strategy_lab.crypto_fleet import prepare, write, digest
from app.strategy_lab.crypto_parameter_checks import CallbackAudit, isolate_native_parameters

PAIRS = [s+'/USDT' for s in ('BTC','ETH','SOL','XRP','DOGE','LINK')]
PRESETS = {
    'E0': ('e0', {'buy_ema_low': .98, 'buy_sma15_32': .98, 'buy_cti_32': -.8,
                  'buy_rsi_fast':50, 'buy_rsi_fast_32':55}),
    'NASOS': ('nasos', {'low_offset':.99, 'low_offset_2':.98, 'ewo_high':2.,
                      'ewo_low':-12., 'rsi_buy':65}),
    'BB': ('bb', {'buy_ema_low':.985, 'buy_ema_high_local_dip':.985,
                 'buy_rsi':32, 'buy_rsi_local_dip':32, 'buy_rsi_fast':50}),
}


def grid():
    rows=[]
    for family,(key,params) in PRESETS.items():
        # Both author entry controls and altered-entry controls are retained.
        rows.append(dict(name=family+'Original',key=key,parameters={},overlay=False,control='original'))
        rows.append(dict(name=family+'EntryControl',key=key,parameters=params,overlay=False,control='entry'))
        for stop in (.02,.04):
            for floor in (.005,.01):
                for target in (.015,.025):
                    rows.append(dict(name=f'Risk{family}S{int(stop*100)}F{int(floor*1000)}T{int(target*1000)}',
                                     key=key,parameters=params,overlay=True,stop=stop,floor=floor,target=target))
    return rows


def prepare_study(root, sources):
    from app.strategy_lab.crypto_fleet_runtime import compile_profile
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    protocol=dict(scope='EXPLORATORY_NATIVE_CRYPTO_PARAMETER_PROTOCOL_V1', grid=grid(),
        development='20240101-20240301',validation='20240301-20240501',holdout='20250101-20250401',
        pairs=PAIRS,fees_per_side=[.004,.008],wallet=10000,max_slots=6,
        selection_gate=dict(closed_win_rate=.6,profit_factor=1.15,maximum_native_drawdown=.15,
                            development_closed=30,validation_closed=20,positive_terminal_pnl=True),
        exploratory=True,prior_2024_results_seen=True,qualification_eligible=False,
        limitations=['Binance USDT candles, not current Kraken USD execution',
                     'Post-period December 2025 metadata; historical execution rules unverified',
                     'No explicit slippage model; fees tested separately',
                     'Force exits excluded from closed win rate; terminal PnL still includes them',
                     'No promise of daily profit or future 60 percent wins'])
    write(root/'protocol.json',protocol)
    prepare(root/'native-fleet',sources)
    folder=root/'strategies';folder.mkdir()
    for key in ('e0','nasos','bb'):
        work=root/'native-fleet'/key;compile_profile(work)
        for p in (work/'strategies').glob('*.py'):shutil.copyfile(p,folder/p.name)
    from app.strategy_lab.crypto_fleet import PROFILES
    for row in protocol['grid']:
        base=PROFILES[row['key']]['strategy']
        body=f'from {base} import {base}\nfrom datetime import timedelta\nclass {row["name"]}({base}):\n'
        body+=f'    buy_params = dict(getattr({base}, "buy_params", {{}}), **{row["parameters"]!r})\n'
        if row['overlay']:
            body+=f'''    stoploss = {-row['stop']!r}
    minimal_roi = {{'0': {row['target']!r}}}
    exit_profit_only = True
    exit_profit_offset = {row['floor']!r}
    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if current_profit >= {row['target']!r}: return 'TUNED_NET_PROFIT_TARGET'
        original = super().custom_exit(pair, trade, current_time, current_rate, current_profit, **kwargs)
        if original and (current_profit < 0 or current_profit >= {row['floor']!r}): return original
        if current_time - trade.open_date_utc >= timedelta(hours=24): return 'TUNED_24H_TIME_EXIT'
'''
        else:body+='    pass\n'
        (folder/(row['name']+'.py')).write_text(body)
    write(root/'implementation-lock.json',{p.name:digest(p) for p in folder.glob('*.py')})
    return protocol


def score(stats):
    trades=stats['trades'];closed=[t for t in trades if not t.get('is_open') and t['exit_reason']!='force_exit']
    gains=sum(max(0,t['profit_abs']) for t in closed);losses=-sum(min(0,t['profit_abs']) for t in closed)
    return dict(closed_trades=len(closed),wins=sum(t['profit_abs']>0 for t in closed),
        closed_win_rate=sum(t['profit_abs']>0 for t in closed)/len(closed) if closed else None,
        closed_net_usdt=sum(t['profit_abs'] for t in closed),
        closed_profit_factor=gains/losses if losses else None,
        force_exit_count=sum(t['exit_reason']=='force_exit' for t in trades),
        terminal_net_usdt=stats['profit_total_abs'],native_drawdown=stats['max_drawdown_account'],
        exits=dict(__import__('collections').Counter(t['exit_reason'] for t in trades)))


def run(root, history, metadata, stage, fee, names=None):
    from app.strategy_lab.nfi_runtime_preflight import disconnected
    from app.strategy_lab.nfi_offline_metadata import load_metadata, exchange_from_recording
    from app.strategy_lab.prepare import config as base_config
    root=Path(root).resolve();history=Path(history).resolve()
    protocol=json.loads((root/'protocol.json').read_text())
    expected=json.loads((root/'implementation-lock.json').read_text())
    if {p.name:digest(p) for p in (root/'strategies').glob('*.py')}!=expected:raise ValueError('FROZEN_IMPLEMENTATION_CHANGED')
    selected=[r for r in protocol['grid'] if names is None or r['name'] in names]
    if not selected or (names and set(names)!={r['name'] for r in selected}):raise ValueError('KNOWN_FROZEN_VARIANTS_REQUIRED')
    if stage=='holdout':
        selection=json.loads((root/'selection.json').read_text())
        if names!=[selection['name']]:raise ValueError('SINGLE_FROZEN_SELECTION_REQUIRED')
    work=root/(stage+'-'+str(fee).replace('.','_'));work.mkdir(exist_ok=False)
    cfg=base_config('offline-parameters')
    for key in ('timeframe','stoploss','use_custom_stoploss','position_adjustment_enable','use_exit_signal',
                'exit_profit_only','ignore_roi_if_entry_signal','order_types'):cfg.pop(key,None)
    cfg.update(timeframe='5m',stake_currency='USDT',dry_run_wallet=10000,stake_amount='unlimited',
               max_open_trades=6,fee=fee,user_data_dir=str(work),strategy_path=str(root/'strategies'),
               db_url='sqlite://',cancel_open_orders_on_exit=False)
    cfg['exchange'].update(name='binance',pair_whitelist=PAIRS)
    write(work/'config.json',cfg)
    exchange=None;audit=CallbackAudit();logger=logging.getLogger('freqtrade.strategy.strategy_wrapper')
    try:
        with disconnected():
            from freqtrade.commands.arguments import Arguments
            from freqtrade.configuration import Configuration
            from freqtrade.configuration.config_validation import validate_config_schema
            from freqtrade.enums import RunMode
            from freqtrade.optimize.backtesting import Backtesting
            argv=['backtesting','--config',str(work/'config.json'),'--strategy-list',*[r['name'] for r in selected],
                  '--strategy-path',str(root/'strategies'),'--user-data-dir',str(work),
                  '--datadir',str(history),'--timerange',protocol[stage],'--cache','none','--export','trades',
                  '--export-directory',str(work/'results'),'--log-file',str(work/'native-backtest.log')]
            config=Configuration(Arguments(argv).get_parsed_arg(),RunMode.BACKTEST).get_config()
            validate_config_schema(config,preliminary=True)
            data,provenance=load_metadata(Path(metadata),PAIRS);write(work/'metadata-context.json',provenance)
            exchange=exchange_from_recording(config,data)
            bt=Backtesting(config,exchange=exchange)
            if bt.fee!=fee:raise ValueError('EXPLICIT_FEE_MISMATCH')
            for strategy,row in zip(bt.strategylist,selected,strict=True):
                if strategy.__class__.__name__!=row['name']:raise ValueError('STRATEGY_ORDER_MISMATCH')
                isolate_native_parameters(strategy)
                for key,value in row['parameters'].items():
                    if abs(getattr(strategy,key).value-value)>1e-10:raise ValueError('NATIVE_PARAMETER_OVERRIDE_FAILED:'+key)
            logger.addHandler(audit)
            try:bt.start()
            finally:logger.removeHandler(audit)
            write(work/'callback-audit.json',audit.report())
            if audit.count:raise ValueError('NATIVE_CALLBACK_ERRORS_INVALIDATE_RESULT')
        archives=list(work.glob('*.zip'))
        if len(archives)!=1:raise ValueError('ONE_NATIVE_ARCHIVE_REQUIRED')
        with zipfile.ZipFile(archives[0]) as z:
            data=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json') and not n.endswith(('.meta.json','_config.json')))))
        report=dict(stage=stage,fee_per_side=fee,network_requests=0,orders=0,qualification_eligible=False,
                    callback_audit=audit.report(),strategies={name:score(stats) for name,stats in data['strategy'].items()})
        write(work/'scores.json',report)
        return report
    finally:
        if exchange is not None:exchange.close()
        write(work/'callback-audit.json',audit.report())


def select(root):
    """Freeze one passing candidate before any later holdout is opened."""
    root=Path(root)
    if (root/'selection.json').exists() or (root/'selection-review.json').exists():
        raise ValueError('SELECTION_ALREADY_FROZEN')
    protocol=json.loads((root/'protocol.json').read_text())
    reports={stage:json.loads((root/(stage+'-0_004')/'scores.json').read_text())
             for stage in ('development','validation')}
    if any(not r['callback_audit']['result_valid'] for r in reports.values()):
        raise ValueError('INVALID_NATIVE_RESULT')
    candidates=[];rejections={}
    for row in protocol['grid']:
        name=row['name'];reasons=[]
        if row.get('control')=='original':continue
        family=next(f for f,(key,_) in PRESETS.items() if key==row['key'])
        for stage,report in reports.items():
            score=report['strategies'][name]
            baseline=report['strategies'][family+'Original']
            minimum=30 if stage=='development' else 20
            if score['closed_trades']<minimum:reasons.append(stage+': too few closed trades')
            if (score['closed_win_rate'] or 0)<.6:reasons.append(stage+': win rate below 60 percent')
            pf=score['closed_profit_factor']
            if pf is None:
                if score['closed_net_usdt']<=0:reasons.append(stage+': no positive closed expectancy')
            elif pf<1.15:reasons.append(stage+': profit factor below 1.15')
            if score['terminal_net_usdt']<=0:reasons.append(stage+': nonpositive terminal PnL')
            if score['native_drawdown']>.15:reasons.append(stage+': native drawdown above 15 percent')
            if score['closed_trades']<=baseline['closed_trades']:reasons.append(stage+': no activity improvement')
        if reasons:rejections[name]=reasons
        else:candidates.append(name)
    winner=max(candidates,key=lambda n:sum(r['strategies'][n]['terminal_net_usdt'] for r in reports.values())) if candidates else None
    result=dict(selected=winner,passing_candidates=candidates,rejections=rejections,
                holdout_opened=False,qualification_eligible=False)
    write(root/'selection-review.json',result)
    if winner:write(root/'selection.json',dict(name=winner,review_sha256=digest(root/'selection-review.json')))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--sources',type=Path);p.add_argument('--history',type=Path);p.add_argument('--metadata',type=Path)
    p.add_argument('--stage',choices=['development','validation','holdout']);p.add_argument('--fee',type=float,default=.004)
    p.add_argument('--names',nargs='+');p.add_argument('--select',action='store_true');a=p.parse_args()
    if a.select:print(json.dumps(select(a.root),indent=2))
    elif a.sources:prepare_study(a.root,a.sources)
    else:run(a.root,a.history,a.metadata,a.stage,a.fee,a.names)

if __name__=='__main__':main()

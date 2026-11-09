"""Native entry-only ROC comparisons on completed frozen Kraken hourly data."""
from pathlib import Path
import argparse,json,logging,shutil,zipfile
from hashlib import sha256
from app.strategy_lab.crypto_fleet import write
from app.strategy_lab.entry_threshold_review import CRYPTO_LEVELS


def run(inputs,output):
    import pandas as pd
    from app.strategy_lab.prepare import config as base_config
    from app.strategy_lab.native_income_ema_review import exchange_from_catalog
    from app.strategy_lab.crypto_parameter_checks import CallbackAudit
    from app.strategy_lab.crypto_parameter_lab import score
    from app.strategy_lab.nfi_runtime_preflight import disconnected
    root=Path(output).resolve();root.mkdir(parents=True,exist_ok=False)
    data=root/'data';data.mkdir();strategies=root/'strategies';strategies.mkdir()
    source=Path(__file__).with_name('FleetROCPullback.py')
    shutil.copyfile(source,strategies/source.name)
    names=[]
    for level in CRYPTO_LEVELS:
        name='ROCDrop'+str(round(level*100));names.append(name)
        (strategies/(name+'.py')).write_text(
            'from FleetROCPullback import FleetROCPullback\n'
            'from app.strategy_lab.broad_strategy_lab import rules,Case\n'
            f'class {name}(FleetROCPullback):\n'
            '    def populate_entry_trend(self,dataframe,metadata):\n'
            '        frame=super().populate_entry_trend(dataframe,metadata)\n'
            f'        frame["enter_long"]=rules(frame,Case("ROC3_PULLBACK",{level!r}))["buy"].astype(int)\n'
            '        return frame\n')
    pairs=[];locks={}
    for path in sorted((Path(inputs)/'current-kraken').glob('*-1h.feather')):
        symbol=path.name.split('-')[0];pair=symbol+'/USD';pairs.append(pair)
        frame=pd.read_feather(path)
        frame=frame[frame.date+pd.Timedelta(hours=1)<=pd.Timestamp('2026-11-08',tz='UTC')].copy()
        if frame.date.duplicated().any() or not frame.date.diff().dropna().eq(pd.Timedelta(hours=1)).all():
            raise ValueError('CONTIGUOUS_COMPLETED_HOURLY_DATA_REQUIRED')
        if frame.date.max()+pd.Timedelta(hours=1)>pd.Timestamp('2026-11-08',tz='UTC'):
            raise ValueError('FROZEN_PERIOD_END_REQUIRED')
        target=data/(symbol+'_USD-1h.feather');frame.to_feather(target)
        locks[target.name]=sha256(target.read_bytes()).hexdigest()
    # Read-only public catalog; no API credentials or order endpoint.
    from urllib.request import urlopen
    with urlopen('https://api.kraken.com/0/public/AssetPairs',timeout=30) as response:
        raw=response.read(8*1024*1024+1)
    if len(raw)>8*1024*1024:raise ValueError('CATALOG_SIZE_BOUND')
    catalog=json.loads(raw);(root/'catalog.json').write_bytes(raw)
    write(root/'plan.json',dict(pairs=pairs,thresholds=CRYPTO_LEVELS,data_sha256=locks,
        source_sha256=sha256(source.read_bytes()).hexdigest(),catalog_sha256=sha256(raw).hexdigest(),
        exits_changed=False,public_requests=1,orders=0,workers_modified=False))
    results=[]
    for fee in (.004,.008):
        work=root/str(fee);work.mkdir();cfg=base_config('entry-threshold-native')
        for key in ('stoploss','use_custom_stoploss','position_adjustment_enable','use_exit_signal',
                    'exit_profit_only','ignore_roi_if_entry_signal','order_types'):
            cfg.pop(key,None)
        cfg.update(timeframe='1h',stake_currency='USD',dry_run_wallet=10000,stake_amount='unlimited',
            max_open_trades=6,fee=fee,user_data_dir=str(work),db_url='sqlite://',cancel_open_orders_on_exit=False,
            dataformat_ohlcv='feather',dataformat_trades='feather')
        cfg['exchange'].update(name='kraken',pair_whitelist=pairs)
        cfg['order_types']=dict(entry='market',exit='market',stoploss='market',stoploss_on_exchange=False)
        write(work/'config.json',cfg);ex=None;audit=CallbackAudit()
        logger=logging.getLogger('freqtrade.strategy.strategy_wrapper')
        try:
            with disconnected():
                from freqtrade.commands.arguments import Arguments
                from freqtrade.configuration import Configuration
                from freqtrade.configuration.config_validation import validate_config_schema
                from freqtrade.enums import RunMode
                from freqtrade.optimize.backtesting import Backtesting
                argv=['backtesting','--config',str(work/'config.json'),'--strategy-list',*names,
                    '--strategy-path',str(strategies),'--datadir',str(data),
                    '--timerange','20261019-20261108','--cache','none','--export','trades',
                    '--export-directory',str(work/'results'),'--log-file',str(work/'native.log')]
                config=Configuration(Arguments(argv).get_parsed_arg(),RunMode.BACKTEST).get_config()
                validate_config_schema(config,preliminary=True)
                ex=exchange_from_catalog(config,catalog);bt=Backtesting(config,exchange=ex)
                if bt.fee!=fee:raise ValueError('EXPLICIT_FEE_REQUIRED')
                logger.addHandler(audit)
                try:bt.start()
                finally:logger.removeHandler(audit)
                if audit.count:raise ValueError('NATIVE_CALLBACK_ERROR')
            archives=list(work.glob('*.zip'))
            if len(archives)!=1:raise ValueError('ONE_NATIVE_EXPORT_REQUIRED')
            with zipfile.ZipFile(archives[0]) as z:
                stats=json.loads(z.read(next(n for n in z.namelist() if n.endswith('.json') and not n.endswith(('.meta.json','_config.json')))))
            results.extend(dict(strategy=n,fee_per_side=fee,**score(s),callback_audit=audit.report())
                for n,s in stats['strategy'].items())
            write(root/'scores.json',dict(cases=results,orders=0,promotion='NONE',
                limitations=['Recent exploratory window only; native no explicit slippage',
                    'Market-fill replay versus paper limit entries; threshold ranking requires forward comparison']))
        finally:
            if ex is not None:ex.close()
            write(work/'callback-audit.json',audit.report())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();run(a.inputs,a.output)

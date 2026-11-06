"""Native Freqtrade dry-run worker with source checks and Atlas evidence capture."""
import argparse
from copy import deepcopy
from datetime import UTC,datetime
from functools import wraps
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import sys
import time

# Also executable as a file inside the pinned official container.
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from app.strategy_lab.nfi_forward_paper import (
    TIMEFRAMES,verify,selected_config,digest,validate_config,write)
from app.strategy_lab.nfi_runtime_preflight import disconnected
from app.strategy_lab.nfi_readiness import primary_activity, source_snapshot


def clean_environment():
    for name in list(os.environ):
        if name.startswith('FREQTRADE__'):del os.environ[name]


def resolve_runtime(work,*,selected=False):
    clean_environment()
    import freqtrade
    from freqtrade.commands.arguments import Arguments
    from freqtrade.configuration import Configuration
    from freqtrade.configuration.config_validation import validate_config_consistency
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver
    if freqtrade.__version__!='2026.9':raise ValueError('PINNED_FREQTRADE_RUNTIME_REQUIRED')
    base=selected_config(work) if selected else verify(work)[0]
    path=work/('resolved-config.json' if selected else 'config.json')
    args=Arguments(['trade','--config',str(path),'--dry-run',
        '--strategy','NostalgiaForInfinityX8','--strategy-path',str(work/'strategies'),
        '--user-data-dir',str(work),'--db-url','sqlite:///'+str(work/'nfi-paper.sqlite3'),
        '--log-file',str(work/'native-paper.log')]).get_parsed_arg()
    config=Configuration(args,RunMode.DRY_RUN).get_config()
    strategy=StrategyResolver.load_strategy(config)
    validate_config_consistency(config)
    if (config['dry_run'] is not True or config['runmode']!=RunMode.DRY_RUN
        or config['exchange']['name']!='kraken' or config['stake_currency']!='USD'
        or config['max_open_trades']!=6 or config['stake_amount']!='unlimited'
        or strategy.timeframe!='5m' or strategy.startup_candle_count!=710
        or strategy.version()!='v18.0.183'
        or config['exchange']['pair_whitelist']!=base['exchange']['pair_whitelist']
        or config['api_server']['enabled'] or config['telegram']['enabled']):
        raise ValueError('RESOLVED_NATIVE_PAPER_CONFIG_MISMATCH')
    return args,config,strategy


def check(work):
    with disconnected():
        _,config,strategy=resolve_runtime(work)
    report=dict(scope='NFI_NATIVE_PAPER_RUNTIME_CHECK_V1',status='RUNTIME_READY',
        dry_run=True,strategy_version=strategy.version(),startup_candles=strategy.startup_candle_count,
        strategy_sha256=digest(work/'strategies/NostalgiaForInfinityX8.py'),
        configured_pairs=len(config['exchange']['pair_whitelist']),
        exchange='kraken',stake_currency='USD',network_requests=0,orders=0)
    write(work/'runtime-check.json',report)
    return report


def preflight(work):
    """Public metadata/current OHLCV only. Never use offline Binance recordings."""
    import ccxt
    import re
    base,manifest=verify(work)
    report=dict(scope='NFI_PUBLIC_KRAKEN_PREFLIGHT_V1',status='FAILED',
        checked_at=datetime.now(UTC).isoformat(),public_api_operations=0,orders=0,
        exchange='kraken',stake_currency='USD',checks=[])
    exchange=ccxt.kraken({'enableRateLimit':True,'timeout':20000,
                         'options':{'maxRetriesOnFailure':0}})
    try:
        report['public_api_operations']+=1
        markets=exchange.load_markets()
        original=base['exchange']['pair_whitelist']
        allowed=[p for p in original if p in markets and markets[p].get('spot') is True
            and markets[p].get('active') is True and not any(
                re.fullmatch(pattern,p) for pattern in base['exchange'].get('pair_blacklist',[]))]
        if 'BTC/USD' not in markets or not markets['BTC/USD'].get('active'):
            raise ValueError('ACTIVE_NATIVE_BTC_USD_INFORMATIVE_REQUIRED')
        if not 10<=len(allowed)<=200:raise ValueError('INSUFFICIENT_ACTIVE_SOURCE_KRAKEN_PAIRS')
        report['public_api_operations']+=1
        tickers=exchange.fetch_tickers(allowed)
        ranked=sorted((p for p in allowed if (tickers.get(p,{}).get('quoteVolume') or 0)>0),
            key=lambda p:(-tickers[p]['quoteVolume'],p))
        if len(ranked)<10:raise ValueError('POSITIVE_OBSERVED_QUOTE_VOLUME_REQUIRED')
        config=deepcopy(base)
        path=work/'resolved-config.json'
        if path.exists() or (work/'resolved-universe.json').exists():
            config=selected_config(work)
            missing=set(config['exchange']['pair_whitelist'])-set(allowed)
            if missing:raise ValueError('SAVED_UNIVERSE_NO_LONGER_ACTIVE_REVIEW_POSITIONS')
        else:
            # Preserve source rules while avoiding a fresh universe dominated
            # by pairs that already fail its USD zero-volume candle check.
            quality={}; selected=[]
            for pair in ranked:
                report['public_api_operations']+=1
                rows=exchange.fetch_ohlcv(pair,'5m',limit=720)
                quality[pair]=primary_activity(rows,datetime.now(UTC).timestamp())
                if quality[pair]['eligible']:selected.append(pair)
                if len(selected)==40:break
            report['primary_activity']=quality
            if len(selected)<10:raise ValueError('INSUFFICIENT_SOURCE_ACTIVITY_QUALIFIED_PAIRS')
            config['exchange']['pair_whitelist']=selected
        sample=['BTC/USD',next(p for p in config['exchange']['pair_whitelist'] if p!='BTC/USD')]
        now=int(datetime.now(UTC).timestamp())
        for pair in sample:
            for tf,seconds in TIMEFRAMES.items():
                report['public_api_operations']+=1
                bars=exchange.fetch_ohlcv(pair,tf,limit=720)
                complete=[b for b in bars if b[0]/1000+seconds<=now]
                if (len(complete)<710 or now-complete[-1][0]/1000-seconds>2*seconds
                    or any(b[0]<=a[0] for a,b in zip(complete,complete[1:]))):
                    raise ValueError('CURRENT_FULL_WARMUP_REQUIRED:'+pair+':'+tf)
                report['checks'].append(dict(pair=pair,timeframe=tf,
                    completed_rows=len(complete),last_open_ms=complete[-1][0]))
                print('NFI_PUBLIC_CANDLES='+pair+'; TF='+tf+'; COMPLETE='+str(len(complete)),flush=True)
        if not path.exists():
            validate_config(config)
            write(path,config)
            write(work/'resolved-universe.json',dict(config_sha256=digest(path),
                base_sha256=manifest['prepared_hashes']['config.json'],
                selection='TOP_40_QUOTE_VOLUME_WITH_SOURCE_USD_ACTIVITY_CHECK_WITHIN_PINNED_UNIVERSE',
                selected_at=report['checked_at'],pairs=config['exchange']['pair_whitelist']))
        report.update(status='PUBLIC_MARKET_READY',selected_pairs=config['exchange']['pair_whitelist'],
                      quote_timestamp_verified=False,
                      limitation='Representative completed-candle checks; native loop validates each pair before entry')
        write(work/'public-markets.json',{p:markets[p] for p in set(config['exchange']['pair_whitelist'])|{'BTC/USD'}})
        write(work/'public-tickers.json',tickers)
    except Exception as exc:
        report['error_type']=type(exc).__name__
        report['error']=str(exc)[:300]
        raise
    finally:
        exchange.close() if hasattr(exchange,'close') else None
        write(work/'market-preflight.json',report)
    return report


def pair_ready(bot,pair,now):
    try:return _pair_ready(bot,pair,now)
    except Exception:return False


def _pair_ready(bot,pair,now):
    """Native raw timestamps stay unchanged. No missing/future candles invented."""
    if bot.config.get('dry_run') is not True:return False
    analyzed,_=bot.dataprovider.get_analyzed_dataframe(pair,'5m')
    if analyzed.empty:return False
    for tf,seconds in TIMEFRAMES.items():
        frame=bot.dataprovider.ohlcv(pair,tf,copy=False)
        if frame.empty:return False
        import pandas as pd
        if not frame['date'].is_monotonic_increasing:return False
        count=int(frame['date'].searchsorted(pd.Timestamp(now).floor('s')-pd.Timedelta(seconds=seconds),side='right'))
        required=bot.strategy.startup_candle_count if tf=='5m' else 1
        if count<required:return False
        latest=frame.iloc[count-1]['date'].timestamp()+seconds
        if not 0<=now.timestamp()-latest<=2*seconds:return False
    latest=analyzed.iloc[-1]['date'].timestamp()+300
    return 0<=now.timestamp()-latest<=600


def block_real_writes(bot):
    def forbidden(*args,**kwargs):raise RuntimeError('REAL_EXCHANGE_WRITES_DISABLED_IN_NFI_PAPER')
    for api in (bot.exchange._api,bot.exchange._api_async):
        for name in ('create_order','cancel_order','cancel_all_orders','edit_order','withdraw',
                     'transfer','borrow_margin','borrow_cross_margin','borrow_isolated_margin'):
            if hasattr(api,name):setattr(api,name,forbidden)


class Evidence:
    def __init__(self,work,baseline):
        from app.asset_modules.daily_research_journal import DailyJournal
        from uuid import uuid4
        self.root=work/('recording-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex)
        self.journal=DailyJournal(self.root,baseline,max_bytes=4*1024**3,max_records=8_000_000)
        self.last={};self.complete_cycle=False;self.error=None
        self._response_hashes={}
        self.source_states={}
        self.signal_snapshots=0
        self.buy_signal_snapshots=0
        self.entry_reviews={}

    def healthy(self):
        status=self.journal.status()
        return self.complete_cycle and not self.error and status['writer_alive'] and status['evidence_complete'] and not status['closed']

    def emit(self,kind,payload):
        if not self.journal.submit(kind,payload):
            self.error='EVIDENCE_SUBMIT_FAILED'
            return False
        return True

    def capture(self,bot,now):
        try:
            pairs=set(bot.active_pair_whitelist)|{'BTC/USD'}
            for pair in sorted(pairs):
                for tf,seconds in TIMEFRAMES.items():
                    frame=bot.dataprovider.ohlcv(pair,tf,copy=False)
                    if frame.empty:continue
                    key=(pair,tf);last=self.last.get(key,-1)
                    import pandas as pd
                    cutoff=pd.Timestamp(now).floor('s')-pd.Timedelta(seconds=seconds)
                    frame=frame[(frame['date']>pd.Timestamp(last,unit='s',tz='UTC')) & (frame['date']<=cutoff)]
                    records=[]
                    for r in frame.itertuples():
                        records.append(dict(date=r.date.isoformat(),open=float(r.open),high=float(r.high),
                            low=float(r.low),close=float(r.close),volume=float(r.volume)))
                    for i in range(0,len(records),200):
                        chunk=records[i:i+200]
                        if not self.emit('NFI_NATIVE_CANDLES',dict(pair=pair,timeframe=tf,rows=chunk,
                            normalization='NATIVE_FREQTRADE_MAY_FILL_ZERO_VOLUME_INTERVALS')):
                            return
                        self.last[key]=datetime.fromisoformat(chunk[-1]['date']).timestamp()
                analyzed,_=bot.dataprovider.get_analyzed_dataframe(pair,'5m')
                if not analyzed.empty:
                    row=analyzed.iloc[-1]
                    at=row['date'].isoformat();key=(pair,'signal')
                    if self.last.get(key)!=at:
                        source=source_snapshot(analyzed)
                        if self.emit('NFI_NATIVE_SIGNAL',dict(pair=pair,date=at,
                            enter_long=str(row.get('enter_long',None)),
                            exit_long=str(row.get('exit_long',None)),
                            enter_tag=str(row.get('enter_tag','')),source_readiness=source)):
                            self.last[key]=at
                            self.source_states[pair]=source
                            self.signal_snapshots+=1
                            self.buy_signal_snapshots+=source.get('buy')==1
            self.complete_cycle=True
        except Exception as exc:
            self.error='CAPTURE_'+type(exc).__name__

    def attach(self,bot,work):
        block_real_writes(bot)
        # Preserve raw CCXT replies before native Freqtrade normalizes candles.
        # The native engine may insert zero-volume intervals; those must remain
        # distinguishable from actual provider OHLCV rows in the evidence.
        if hasattr(bot.exchange._api_async,'fetch_ohlcv'):
            original_fetch=bot.exchange._api_async.fetch_ohlcv
            @wraps(original_fetch)
            async def raw_ohlcv(symbol,timeframe='1m',*args,**kwargs):
                began=time.monotonic()
                result=await original_fetch(symbol,timeframe,*args,**kwargs)
                for offset in range(0,len(result),200):
                    self.emit('NFI_RAW_PUBLIC_OHLCV',dict(pair=symbol,timeframe=timeframe,
                        received_at=datetime.now(UTC).isoformat(),
                        latency_ms=(time.monotonic()-began)*1000,
                        rows=deepcopy(result[offset:offset+200]),provider_timestamps_modified=False))
                return result
            bot.exchange._api_async.fetch_ohlcv=raw_ohlcv
        original=bot.execute_entry
        @wraps(original)
        def entry(pair,stake_amount,*args,**kwargs):
            reason=('OPERATOR_ENTRY_PAUSE' if (work/'PAUSE_NEW_ENTRIES').exists() else
                    'CAPTURE_NOT_READY' if not self.healthy() else
                    'NATIVE_CANDLES_NOT_READY' if not pair_ready(bot,pair,datetime.now(UTC)) else 'NATIVE_ENTRY_ALLOWED')
            allowed=reason=='NATIVE_ENTRY_ALLOWED'
            payload=dict(pair=pair,stake_amount=float(stake_amount),allowed=allowed,reason=reason,
                         trade_id=getattr(kwargs.get('trade'),'id',None))
            if not self.emit('NFI_ENTRY_REVIEW',payload):return False
            self.entry_reviews[reason]=self.entry_reviews.get(reason,0)+1
            if not allowed:return False
            return original(pair,stake_amount,*args,**kwargs)
        bot.execute_entry=entry
        for name in ('fetch_l2_order_book','fetch_ticker','get_tickers'):
            if not hasattr(bot.exchange,name):continue
            original_call=getattr(bot.exchange,name)
            def wrap(call,method):
                @wraps(call)
                def observed(*args,**kwargs):
                    began=time.monotonic();result=call(*args,**kwargs)
                    try:
                        if method=='get_tickers':
                            allowed=set(bot.active_pair_whitelist)|{'BTC/USD'}
                            parts=[(pair,result[pair]) for pair in sorted(allowed & result.keys())]
                        else:parts=[(str(args[0]) if args else method,result)]
                        for subject,response in parts:
                            fingerprint=sha256(json.dumps(response,sort_keys=True,allow_nan=False).encode()).hexdigest()
                            key=(method,subject)
                            if self._response_hashes.get(key)==fingerprint:continue
                            if self.emit('NFI_NATIVE_PUBLIC_RESPONSE',dict(method=method,subject=subject,
                                received_at=datetime.now(UTC).isoformat(),latency_ms=(time.monotonic()-began)*1000,
                                response=deepcopy(response))):self._response_hashes[key]=fingerprint
                    except Exception as exc:self.error='PUBLIC_CAPTURE_'+type(exc).__name__
                    return result
                return observed
            setattr(bot.exchange,name,wrap(original_call,name))


def refresh_universe(work,token):
    """One explicit refresh, only with no open trade/order; keep the same wallet."""
    import re
    import sqlite3
    import shutil
    if not re.fullmatch('[0-9a-f]{32}',token):raise ValueError('REFRESH_TOKEN_REQUIRED')
    marker=work/('universe-refresh-'+token+'.json')
    if marker.exists():return
    selected_config(work)
    ledger=work/'nfi-paper.sqlite3'
    if ledger.exists():
        db=sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            if db.execute('SELECT COUNT(*) FROM trades WHERE is_open=1').fetchone()[0]:
                raise ValueError('UNIVERSE_REFRESH_REQUIRES_NO_OPEN_POSITIONS')
            if db.execute('SELECT COUNT(*) FROM orders WHERE ft_is_open=1').fetchone()[0]:
                raise ValueError('UNIVERSE_REFRESH_REQUIRES_NO_WORKING_ORDERS')
        finally:db.close()
    backup=work/('universe-before-'+token)
    if backup.exists():
        for name in ('resolved-config.json','resolved-universe.json'):
            if (backup/name).read_bytes()!=(work/name).read_bytes():
                raise ValueError('PREVIOUS_UNIVERSE_REFRESH_REQUIRES_REVIEW')
    else:backup.mkdir()
    names=('resolved-config.json','resolved-universe.json')
    for name in names:shutil.copyfile(work/name,backup/name)
    try:
        for name in names:(work/name).unlink()
        preflight(work)
        write(marker,dict(status='REFRESH_COMPLETE',wallet_reset=False,source_modified=False))
    except BaseException:
        for name in names:shutil.copyfile(backup/name,work/name)
        raise


def run(work,*,refresh_token=None):
    # Native Linux container lock supplements the unique Docker container name.
    # Keep the descriptor alive until the entire native worker has stopped.
    import fcntl
    lock=(work/'engine.lock').open('a')
    try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise ValueError('NFI_PAPER_ALREADY_RUNNING') from None
    try:
        if refresh_token:refresh_universe(work,refresh_token)
        preflight(work)
        return _run_locked(work)
    finally:
        fcntl.flock(lock.fileno(),fcntl.LOCK_UN);lock.close()


def _run_locked(work):
    from freqtrade.enums import State
    from freqtrade.worker import Worker
    from app.strategy_lab.nfi_forward_status import ledger_status
    args,config,_=resolve_runtime(work,selected=True)
    ledger_status(work) # Refuse an unrelated existing database before native initialization.
    evidence=Evidence(work,digest(work/'resolved-config.json'))
    worker=None
    def stop(signum,frame):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,stop)
    write(work/'recording-session.json',dict(session=str(evidence.root),
        config_sha256=digest(work/'resolved-config.json'),source_manifest_sha256=digest(work/'paper-manifest.json')))
    try:
        worker=Worker(args,config=config)
        evidence.attach(worker.freqtrade,work)
        state=None
        while True:
            state=worker._worker(old_state=state)
            bot=worker.freqtrade;now=datetime.now(UTC)
            evidence.capture(bot,now)
            status=dict(scope='NFI_FORWARD_PAPER_STATUS_V1',updated_at=now.isoformat(),
                recording_id=evidence.root.name,
                state=str(state),dry_run=True,exchange='kraken',stake_currency='USD',
                strategy_version=bot.strategy.version(),active_pairs=len(bot.active_pair_whitelist),
                pause_new_entries=(work/'PAUSE_NEW_ENTRIES').exists(),
                capture=evidence.journal.status(),capture_error=evidence.error,
                fresh_ready_pairs=sum(pair_ready(bot,p,now) for p in bot.active_pair_whitelist),
                live_trading_enabled=False)
            from collections import Counter
            status['source_readiness']=dict(
                latest_counts=dict(Counter(s['state'] for s in evidence.source_states.values())),
                by_pair=evidence.source_states,signal_snapshots=evidence.signal_snapshots,
                buy_signal_snapshots=evidence.buy_signal_snapshots,
                entry_reviews=evidence.entry_reviews,
                note='Fresh data is not a source BUY signal. Repeated snapshots are not win-rate samples.')
            try:status.update(ledger_status(work))
            except Exception as exc:status['ledger_status_error']=type(exc).__name__
            try:
                temporary=work/'engine-status.json.tmp'
                write(temporary,status);temporary.replace(work/'engine-status.json')
            except OSError:evidence.error='STATUS_WRITE_FAILED'
            if state not in (State.RUNNING,State.PAUSED):
                print('NFI_NATIVE_STATE='+str(state)+'; SEE_NATIVE_LOG; NO_AUTOMATIC_RESTART',flush=True)
                break
    except KeyboardInterrupt:
        print('NFI_PAPER_STOPPED; DATABASE_PRESERVED; OPEN_SIMULATED_POSITIONS_NEED_RESTART_FOR_MANAGEMENT',flush=True)
    except Exception as exc:
        evidence.error='NATIVE_'+type(exc).__name__
        raise
    finally:
        close_capture(work,worker,evidence)


def close_capture(work,worker,evidence):
    cleanup_error=None
    try:
        if worker is not None:worker.exit()
    except BaseException as exc:
        cleanup_error=type(exc).__name__
        raise
    finally:
        evidence.journal.close()
        write(work/'capture-final.json',dict(recording_id=evidence.root.name,
            journal=evidence.journal.status(),error=evidence.error,cleanup_error=cleanup_error))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,default=Path('/work'))
    p.add_argument('--check-only',action='store_true');p.add_argument('--preflight',action='store_true')
    p.add_argument('--refresh-universe-once')
    a=p.parse_args();work=a.work.resolve()
    if a.check_only:result=check(work)
    elif a.preflight:result=preflight(work)
    else:
        clean_environment();run(work,refresh_token=a.refresh_universe_once);return
    print('NFI_PAPER='+result['status'])


if __name__=='__main__':main()

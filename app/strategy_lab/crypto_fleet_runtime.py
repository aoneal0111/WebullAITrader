"""Sealed native Freqtrade PAPER workers; no Atlas GUI or Grok launch path."""
import argparse
from collections import Counter
from datetime import UTC, datetime
from functools import wraps
import json
from pathlib import Path
import signal
import time

from app.strategy_lab.crypto_fleet import PROFILES, SOURCES, MODULE, digest, write
from app.strategy_lab.nfi_forward_engine import Evidence, clean_environment
from app.strategy_lab.nfi_forward_status import ledger_status

# Add-on definitions do not alter the original five-profile fleet.
DEFINITIONS = {**PROFILES, 'roc': dict(strategy='FleetROCPullback', timeframe='1h', startup=200)}


def repair_bb_tag_names(converted):
    """Finish the v3 tag migration without touching pinned upstream bytes."""
    import io
    import token
    import tokenize
    result = []
    for item in tokenize.generate_tokens(io.StringIO(converted).readline):
        if item.type == token.NAME and item.string == 'buy_tag':
            item = tokenize.TokenInfo(item.type, 'enter_tag', item.start, item.end, item.line)
        result.append(item)
    return tokenize.untokenize(result)


def verify(work, *, compiled=True):
    work=Path(work);profile=json.loads((work/'profile.json').read_text())
    if profile.get('scope')!='NATIVE_CRYPTO_FLEET_PROFILE_V1' or profile.get('key') not in DEFINITIONS:
        raise ValueError('FLEET_PROFILE_REQUIRED')
    key=profile['key']
    expected={Path(name).name:sha for group,_,_,name,sha in SOURCES if group==key}
    if profile['source_files']!=expected:raise ValueError('PINNED_SOURCE_IDENTITIES_REQUIRED')
    for name,sha in expected.items():
        p=work/'upstream'/name
        if p.is_symlink() or digest(p)!=sha:raise ValueError('UPSTREAM_CHANGED')
    config=json.loads((work/'config.json').read_text())
    if digest(work/'config.json')!=profile['config_sha256']:raise ValueError('CONFIG_CHANGED')
    if (config.get('dry_run') is not True or config.get('trading_mode')!='spot'
        or config.get('stake_currency')!='USD' or config.get('stake_amount')!='unlimited'
        or config.get('max_open_trades')!=6 or config.get('dry_run_wallet')!=10000
        or config.get('strategy')!=DEFINITIONS[key]['strategy']
        or config.get('db_url')!='sqlite:////work/paper.sqlite3'
        or config.get('force_entry_enable') is not False
        or config['exchange']['name']!='kraken'
        or config['exchange']['pair_whitelist']!=profile['pairs']
        or config['telegram']['enabled'] is not False or config['api_server']['enabled'] is not False
        or any(config['exchange'].get(k) for k in ('key','secret','password','uid','privateKey','walletAddress'))):
        raise ValueError('SEALED_SPOT_PAPER_CONFIG_REQUIRED')
    if compiled:
        if not profile.get('compiled') or not profile.get('generated_files'):raise ValueError('COMPILE_FIRST')
        actual={p.name:digest(p) for p in (work/'strategies').glob('*.py')}
        if actual!=profile['generated_files']:raise ValueError('COMPILED_STRATEGY_CHANGED')
    return config,profile


def compile_profile(work):
    from freqtrade.strategy.strategyupdater import StrategyUpdater
    import freqtrade
    if freqtrade.__version__!='2026.9':raise ValueError('PINNED_FREQTRADE_2026_9_REQUIRED')
    work=Path(work);_,profile=verify(work,compiled=False)
    if profile.get('compiled'):verify(work);return profile
    folder=work/'strategies';folder.mkdir(exist_ok=False)
    key=profile['key'];definition=DEFINITIONS[key]
    if key in ('e0','nasos','bb'):
        original=next((work/'upstream').glob('*.py')).read_text()
        converted=StrategyUpdater().update_code(original)
        if key=='bb':
            # NumPy 2 refuses the old string/NaN dtype promotion. Object
            # dtype retains the original categorical values and missing NaNs.
            categorical="np.where(mavalue < pm_arr, 'down', 'up')"
            if converted.count(categorical)!=1:raise ValueError('EXACT_BB_PMAX_ADAPTER_REQUIRED')
            converted=converted.replace(categorical,categorical+'.astype(object)')
            # Upstream accidentally assigns a 5m ROC to 1h rows by integer
            # index. Use the actual informative candles, avoiding dependence
            # on how many primary candles happen to be loaded.
            roc="informative_1h['roc'] = ta.ROC(dataframe, timeperiod=9)"
            if converted.count(roc)!=1:raise ValueError('EXACT_BB_ROC_ADAPTER_REQUIRED')
            converted=converted.replace(roc,"informative_1h['roc'] = ta.ROC(informative_1h, timeperiod=9)")
            converted=repair_bb_tag_names(converted)
        reference=definition['strategy']+'Reference'
        (folder/(reference+'.py')).write_text(converted,encoding='utf-8')
        (folder/(definition['strategy']+'.py')).write_text(
            'from '+reference+' import '+definition['source_class']+'\n'
            'class '+definition['strategy']+'('+definition['source_class']+'):\n'
            '    INTERFACE_VERSION = 3\n    startup_candle_count = 640\n    can_short = False\n',encoding='utf-8')
    elif key=='crsi':
        original=(work/'upstream/strategy.py').read_text()
        before="df['sma_200'] = df['sma_200'].bfill()"
        if original.count(before)!=1:raise ValueError('EXACT_CRSI_CAUSAL_ADAPTER_REQUIRED')
        (folder/'FleetCRSIReference.py').write_text(original.replace(before,"df['sma_200'] = df['sma_200'].copy()"))
        (folder/'FleetCRSI.py').write_bytes((MODULE/'FleetCRSI.py').read_bytes())
    elif key=='roc':
        (folder/'FleetROCPullback.py').write_bytes((MODULE/'FleetROCPullback.py').read_bytes())
    else:
        for old,new in [('config.py','FleetWaveConfig.py'),('indicators.py','FleetWaveIndicators.py'),
                        ('strategies.py','FleetWaveReference.py')]:
            content=(work/'upstream'/old).read_text()
            content=content.replace('from config import ', 'from FleetWaveConfig import ')
            content=content.replace('from indicators import ', 'from FleetWaveIndicators import ')
            (folder/new).write_text(content,encoding='utf-8')
        (folder/'FleetWave.py').write_bytes((MODULE/'FleetWave.py').read_bytes())
    profile.update(compiled=True, freqtrade_version=freqtrade.__version__,
        generated_files={p.name:digest(p) for p in folder.glob('*.py')})
    write(work/'profile.json',profile)
    return profile


def resolve(work):
    from freqtrade.commands.arguments import Arguments
    from freqtrade.configuration import Configuration
    from freqtrade.configuration.config_validation import validate_config_consistency
    from freqtrade.enums import RunMode
    from freqtrade.resolvers import StrategyResolver
    import freqtrade
    if freqtrade.__version__!='2026.9':raise ValueError('PINNED_FREQTRADE_2026_9_REQUIRED')
    work=Path(work);clean_environment();_,profile=verify(work)
    args=Arguments(['trade','--config',str(work/'config.json'),'--dry-run',
        '--strategy',profile['strategy'],'--strategy-path',str(work/'strategies'),
        '--user-data-dir',str(work),'--db-url','sqlite:///'+str(work/'paper.sqlite3'),
        '--log-file',str(work/'native-paper.log')]).get_parsed_arg()
    config=Configuration(args,RunMode.DRY_RUN).get_config()
    strategy=StrategyResolver.load_strategy(config)
    validate_config_consistency(config)
    if (config['dry_run'] is not True or config['runmode']!=RunMode.DRY_RUN
            or config['trading_mode'].value!='spot' or strategy.can_short
            or strategy.timeframe!=profile['timeframe'] or strategy.startup_candle_count!=DEFINITIONS[profile['key']].get('startup',640)
            or config['exchange']['name']!='kraken' or config['stake_currency']!='USD'):
        raise ValueError('RESOLVED_NATIVE_PAPER_MISMATCH')
    return args,config,strategy,profile


def check(work):
    from app.strategy_lab.nfi_runtime_preflight import disconnected
    with disconnected():
        compile_profile(work)
        _,_,strategy,profile=resolve(work)
    result=dict(status='RUNTIME_READY',profile=profile['key'],strategy=profile['strategy'],
        timeframe=strategy.timeframe,startup_candles=strategy.startup_candle_count,network_requests=0,orders=0,
        published_performance_applies=False,live_trading_enabled=False)
    write(Path(work)/'runtime-check.json',result)
    return result


def frames_for(key):
    return ({'1h':3600} if key=='roc' else {'5m':300,'1h':3600} if key=='bb' else {'5m':300}
            if key in ('e0','nasos') else {'4h':14400})


def ready(bot,pair,now,key):
    try:
        if bot.config.get('dry_run') is not True:return False
        import pandas as pd
        intervals=frames_for(key);primary=DEFINITIONS[key]['timeframe']
        for symbol in ([pair,'BTC/USD'] if key=='wave' else [pair]):
            for tf,seconds in intervals.items():
                data=bot.dataprovider.ohlcv(symbol,tf,copy=False)
                if data.empty or not data['date'].is_monotonic_increasing or data['date'].duplicated().any():return False
                cutoff=pd.Timestamp(now)-pd.Timedelta(seconds=seconds)
                data=data[data['date']<=cutoff]
                if len(data)<DEFINITIONS[key].get('startup',640) or not 0<=now.timestamp()-data.iloc[-1]['date'].timestamp()-seconds<=2*seconds:return False
        analyzed,_=bot.dataprovider.get_analyzed_dataframe(pair,primary)
        return (not analyzed.empty and 0<=now.timestamp()-analyzed.iloc[-1]['date'].timestamp()
                -intervals[primary]<=2*intervals[primary])
    except Exception:return False


def snapshot(frame,*,strategy=None):
    row=frame.iloc[-1];value=row.get('enter_long')
    import pandas as pd
    import math
    buy=None if value is None or pd.isna(value) else int(value)
    inputs={}
    for name in ('close','volume','rsi','rsi2','rsi_fast','rsi_slow','cti','EWO','roc3',
                 'ema_8','ema_16','sma_15','sma200','sma_200','atr'):
        value=row.get(name)
        if value is not None and pd.notna(value) and math.isfinite(float(value)):
            inputs[name]=float(value)
    params={}
    if strategy is not None:
        for name in ('buy_ema_low','buy_sma15_32','buy_cti_32','buy_rsi_fast',
                     'buy_rsi_fast_32','buy_ewo','buy_ema_high','buy_rsi','buy_rsi_32',
                     'low_offset','low_offset_2','ewo_high','ewo_low','rsi_buy'):
            parameter=getattr(strategy,name,None)
            if parameter is not None:params[name]=getattr(parameter,'value',parameter)
    return dict(state='SOURCE_BUY_SIGNAL' if buy==1 else 'NO_SOURCE_BUY_SIGNAL',
        buy=buy,candle_at=row['date'].isoformat(),enter_tag=str(row.get('enter_tag','')),
        entry_inputs=inputs,entry_parameters=params)


def rate_gate(path):
    """One public-request pacing file shared by all fleet containers."""
    import fcntl
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+') as file:
        fcntl.flock(file.fileno(),fcntl.LOCK_EX)
        try:
            file.seek(0)
            try: previous=float(file.read() or 0)
            except ValueError: previous=0
            now=time.monotonic()
            # Monotonic epochs can reset after a host reboot.
            if previous>now:previous=0
            time.sleep(max(0,1.1-(now-previous)))
            file.seek(0);file.truncate();file.write(str(time.monotonic()));file.flush()
        finally:fcntl.flock(file.fileno(),fcntl.LOCK_UN)


def pace_public_requests(path):
    import ccxt
    import ccxt.async_support as async_ccxt
    import asyncio
    original=ccxt.kraken.fetch
    @wraps(original)
    def paced(self,*args,**kwargs):
        rate_gate(path);return original(self,*args,**kwargs)
    ccxt.kraken.fetch=paced
    original_async=async_ccxt.kraken.fetch
    @wraps(original_async)
    async def paced_async(self,*args,**kwargs):
        await asyncio.to_thread(rate_gate,path)
        return await original_async(self,*args,**kwargs)
    async_ccxt.kraken.fetch=paced_async


def capture_native_actions(bot,evidence):
    for name in ('create_order','fetch_order','cancel_order'):
        if not hasattr(bot.exchange,name):continue
        original=getattr(bot.exchange,name)
        def wrap(call,method):
            @wraps(call)
            def observed(*args,**kwargs):
                result=call(*args,**kwargs)
                fingerprint=digest_payload(result)
                key=('order',method,str(result.get('id','')) if isinstance(result,dict) else str(args[:1]))
                if evidence.last.get(key)!=fingerprint:
                    evidence.emit('FLEET_PAPER_ORDER_RESPONSE',dict(method=method,response=result,native_dry_run=True))
                    evidence.last[key]=fingerprint
                return result
            return observed
        setattr(bot.exchange,name,wrap(original,name))


def digest_payload(value):
    from hashlib import sha256
    return sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def run(work,shared):
    import fcntl
    from freqtrade.worker import Worker
    from freqtrade.enums import State
    work=Path(work);shared=Path(shared)
    with (work/'engine.lock').open('a') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise ValueError('PROFILE_ALREADY_RUNNING') from None
        args,config,strategy,profile=resolve(work)
        def request_stop(signum,frame):raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,request_stop)
        evidence=Evidence(work,digest(work/'config.json'),timeframes=frames_for(profile['key']),
            primary=profile['timeframe'],ready=lambda bot,pair,now:ready(bot,pair,now,profile['key']),
            snapshot=lambda frame:snapshot(frame,strategy=strategy),prefix='FLEET')
        worker=None;failed=None
        status=dict(scope='FLEET_NATIVE_PAPER_STATUS_V1',state='STARTING',
            updated_at=datetime.now(UTC).isoformat(),recording_id=evidence.root.name,
            live_trading_enabled=False,capture=evidence.journal.status())
        write(work/'engine-status.json',status)
        write(work/'recording-session.json',dict(session=str(evidence.root)))
        try:
            pace_public_requests(shared/'public-request-clock')
            worker=Worker(args,config=config)
            bot=worker.freqtrade;evidence.attach(bot,work);capture_native_actions(bot,evidence)
            state=None
            while not (work/'STOP').exists():
                state=worker._worker(old_state=state)
                now=datetime.now(UTC);evidence.capture(bot,now)
                status.update(state=str(state),updated_at=now.isoformat(),
                    capture=evidence.journal.status(),capture_error=evidence.error,
                    pause_new_entries=(work/'PAUSE_NEW_ENTRIES').exists(),
                    fresh_ready_pairs=sum(ready(bot,p,now,profile['key']) for p in bot.active_pair_whitelist),
                    source_readiness=dict(latest_counts=dict(Counter(s['state'] for s in evidence.source_states.values())),
                        signal_snapshots=evidence.signal_snapshots,buy_signal_snapshots=evidence.buy_signal_snapshots,
                        entry_reviews=evidence.entry_reviews))
                try:status.update(ledger_status(work,filename='paper.sqlite3'))
                except Exception as exc:status['ledger_status_error']=type(exc).__name__
                temporary=work/'engine-status.json.tmp';write(temporary,status);temporary.replace(work/'engine-status.json')
                if state not in (State.RUNNING,State.PAUSED):break
        except KeyboardInterrupt:status['state']='STOPPED'
        except Exception as exc:
            failed=type(exc).__name__;evidence.error='NATIVE_'+failed;status['state']='FAILED'
            raise
        finally:
            try:
                if worker is not None:worker.exit()
            finally:
                evidence.journal.close()
                status.update(updated_at=datetime.now(UTC).isoformat(),capture=evidence.journal.status(),capture_error=evidence.error)
                write(work/'engine-status.json',status)
                write(work/'capture-final.json',dict(recording_id=evidence.root.name,
                    journal=evidence.journal.status(),error=evidence.error))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--shared',type=Path)
    p.add_argument('--check',action='store_true');p.add_argument('--nfi',action='store_true');a=p.parse_args()
    if a.nfi:
        from app.strategy_lab.nfi_forward_engine import run as run_nfi
        clean_environment();pace_public_requests((a.shared or a.work.parent/'shared')/'public-request-clock')
        run_nfi(a.work);return
    if a.check:print(json.dumps(check(a.work),indent=2))
    else:run(a.work,a.shared or a.work.parent/'shared')


if __name__=='__main__':main()

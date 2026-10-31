"""Bounded public-candle capture and disconnected NFI signal comparison.

No trading loop, order API, model client, or installed account database is used.
"""
import argparse
import ast
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import time
import urllib.parse
import urllib.request

LOCK=json.loads(Path(__file__).with_name('source-lock.json').read_text(encoding='utf-8'))
PAIRS=('BTC/USD','ETH/USD','SOL/USD','LINK/USD','LTC/USD','AVAX/USD','XRP/USD','ADA/USD')
INTERVALS={'5m':5,'15m':15,'1h':60,'4h':240,'1d':1440}
MAX_READS=42
MAX_BYTES=2*1024*1024


def digest(path):return sha256(Path(path).read_bytes()).hexdigest()


def verify_lab(lab):
    lab=Path(lab).resolve()
    m=json.loads((lab/'lab-manifest.json').read_text(encoding='utf-8'))
    if m.get('source_lock')!=LOCK or m.get('scope')!='ISOLATED_FREQTRADE_DRY_RUN_LAB_V1':
        raise ValueError('PINNED_LAB_REQUIRED')
    expected={'user_data/runtime_check.py','compose.json','user_data/config.json',
              'user_data/strategies/AtlasNFILab.py','user_data/strategies/'+LOCK['strategy_file'],
              'user_data/strategies/LICENSE'}
    if set(m.get('files',{}))!=expected:raise ValueError('LAB_FILES_MISMATCH')
    for name,expected_hash in m['files'].items():
        p=lab/name
        if p.is_symlink() or not p.resolve().is_relative_to(lab) or digest(p)!=expected_hash:
            raise ValueError('LAB_FILES_CHANGED')
    if digest(lab/'user_data/strategies'/LOCK['strategy_file'])!=LOCK['strategy_sha256']:
        raise ValueError('UPSTREAM_HASH_MISMATCH')
    return m


def write(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def read_public(operation,params):
    url='https://api.kraken.com/0/public/'+operation+'?'+urllib.parse.urlencode({**params,'nonce':time.time_ns()})
    with urllib.request.urlopen(url,timeout=15) as response:
        data=response.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError('RESPONSE_BOUND')
    payload=json.loads(data)
    if payload.get('error'):raise ValueError('PUBLIC_API_ERROR')
    return payload['result']


def normalize(rows,minutes,server_now):
    if not isinstance(rows,list) or len(rows)>721:raise ValueError('CANDLE_COUNT_BOUND')
    result=[];previous=None
    for row in rows:
        if not isinstance(row,list) or len(row)!=8:raise ValueError('CANDLE_SCHEMA')
        stamp=float(row[0]);values=[float(row[i]) for i in (1,2,3,4,6)]
        if not all(math.isfinite(x) for x in [stamp,*values]):raise ValueError('NONFINITE_CANDLE')
        if stamp!=int(stamp) or int(stamp)%(minutes*60):raise ValueError('CANDLE_ALIGNMENT')
        if previous is not None and stamp<=previous:raise ValueError('CANDLE_ORDER')
        previous=stamp
        o,h,l,c,v=values
        if min(o,h,l,c)<=0 or v<0 or h<max(o,l,c) or l>min(o,h,c):raise ValueError('CANDLE_OHLC')
        # Drop uncommitted candles using one frozen exchange-server cutoff.
        if stamp+minutes*60>server_now:continue
        result.append([int(stamp),o,h,l,c,v])
    return result


def capture(lab,output,reader=read_public,pause=time.sleep):
    m=verify_lab(lab);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    manifest={'scope':'FROZEN_NFI_PUBLIC_CANDLES_V1','source_lock':LOCK,
              'lab_manifest_sha256':digest(Path(lab)/'lab-manifest.json'),
              'status':'PARTIAL','reads':0,'files':{},'skipped_pairs':[],
              'grok_requests':0,'orders':0,'atlas_baseline_modified':False}
    def read(operation,params):
        if manifest['reads']>=MAX_READS:raise ValueError('READ_BUDGET')
        manifest['reads']+=1
        return reader(operation,params)
    try:
        server_now=int(read('Time',{})['unixtime'])
        if abs(datetime.now(UTC).timestamp()-server_now)>30:raise ValueError('SERVER_CLOCK_DISAGREEMENT')
        manifest['server_cutoff_utc']=datetime.fromtimestamp(server_now,UTC).isoformat()
        markets=read('AssetPairs',{})
        available={}
        for market in markets.values():
            name=market.get('wsname','').replace('XBT/','BTC/')
            if name in PAIRS and market.get('status')=='online':available[name]=market['altname']
        for pair in PAIRS:
            if pair not in available:
                manifest['skipped_pairs'].append({'pair':pair,'reason':'NOT_IN_ONLINE_PUBLIC_PAIR_METADATA'});continue
            for tf,minutes in INTERVALS.items():
                pause(1)
                payload=read('OHLC',{'pair':available[pair],'interval':minutes})
                keys=[k for k in payload if k!='last']
                if len(keys)!=1:raise ValueError('PAIR_RESPONSE_SCHEMA')
                rows=normalize(payload[keys[0]],minutes,server_now)
                name=pair.replace('/','_')+'-'+tf+'.json'
                write(output/name,rows)
                manifest['files'][name]={'sha256':digest(output/name),'pair':pair,'timeframe':tf,'rows':len(rows)}
                print('CAPTURE_FRAMES='+str(len(manifest['files'])),flush=True)
        if not all(any(f['pair']==p for f in manifest['files'].values()) for p in PAIRS[:2]):
            raise ValueError('BASELINE_PAIR_MISSING')
        manifest['status']='COMPLETE'
    except Exception as exc:
        manifest['error_type']=type(exc).__name__
        raise
    finally:write(output/'capture-manifest.json',manifest)
    return manifest


def condition_source_lines(path):
    """Map diagnostic component ordinals to the exact pinned source lines."""
    tree=ast.parse(Path(path).read_text(encoding='utf-8'))
    function=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='populate_entry_trend')
    def is_append(n):
        return (isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                and n.func.attr=='append' and isinstance(n.func.value,ast.Name)
                and n.func.value.id=='long_entry_logic')
    appends=sorted((n for n in ast.walk(function) if is_append(n)),key=lambda n:n.lineno)
    result={}
    for node in ast.walk(function):
        if not isinstance(node,ast.If) or not isinstance(node.test,ast.Compare):continue
        test=node.test
        if (isinstance(test.left,ast.Name) and test.left.id=='long_entry_condition_index'
                and len(test.comparators)==1 and isinstance(test.comparators[0],ast.Constant)):
            inner=sorted((n.lineno for n in ast.walk(node) if is_append(n)))
            result[str(test.comparators[0].value)]=[appends[0].lineno,*inner,appends[-1].lineno]
    return result


def trace_signals(strategy,module,frame,pair,startup):
    """Observe the unchanged AND reducer, then restore it even on native failure."""
    import sys
    import numpy as np
    original=module._and_entry_conditions;records=[]
    count=len(frame);eligible=max(0,count-startup)
    def tail(value):
        array=np.asarray(value,dtype=bool)
        if array.ndim==0:array=np.full(count,bool(array))
        if array.shape!=(count,):raise ValueError('MASK_SHAPE')
        return array[startup:]
    def traced(conditions):
        result=original(conditions)
        caller=sys._getframe(1).f_locals
        if conditions is caller.get('long_entry_logic'):
            records.append({'signal_id':int(caller['long_entry_condition_index']),
                'signal_rows':int(tail(result).sum()),
                'component_count':len(conditions),
                'component_fail_rows':[int((~tail(c)).sum()) for c in conditions]})
        return result
    module._and_entry_conditions=traced
    try:result=strategy.populate_entry_trend(frame,{'pair':pair})
    finally:module._and_entry_conditions=original
    return result,{'eligible_rows':eligible,'conditions':records}


def analyze(lab,output):
    import socket
    def forbidden(*args,**kwargs):raise RuntimeError('NETWORK_DISABLED_DURING_SIGNAL_ANALYSIS')
    socket.socket.connect=forbidden;socket.socket.connect_ex=forbidden
    socket.socket.sendto=forbidden;socket.getaddrinfo=forbidden
    import sys
    import tempfile
    import pandas as pd
    import freqtrade
    from freqtrade.enums import RunMode
    from freqtrade.persistence import init_db
    from freqtrade.resolvers import StrategyResolver
    if freqtrade.__version__!='2026.9':raise ValueError('PINNED_FREQTRADE_REQUIRED')
    lab,output=Path(lab).resolve(),Path(output).resolve();verify_lab(lab)
    manifest=json.loads((output/'capture-manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('status')!='COMPLETE' or manifest.get('scope')!='FROZEN_NFI_PUBLIC_CANDLES_V1'
            or manifest.get('source_lock')!=LOCK or manifest.get('lab_manifest_sha256')!=digest(lab/'lab-manifest.json')
            or len(manifest.get('files',{}))>40 or not 2<=manifest.get('reads',0)<=MAX_READS):raise ValueError('FROZEN_CAPTURE_REQUIRED')
    frames={}
    cutoff=datetime.fromisoformat(manifest['server_cutoff_utc']).timestamp()
    for name,item in manifest['files'].items():
        if item.get('pair') not in PAIRS or item.get('timeframe') not in INTERVALS:
            raise ValueError('CAPTURE_PAIR_OR_TIMEFRAME')
        expected=item['pair'].replace('/','_')+'-'+item['timeframe']+'.json'
        path=output/name
        if name!=expected or path.is_symlink() or path.stat().st_size>MAX_BYTES or digest(path)!=item['sha256']:
            raise ValueError('CAPTURE_CHANGED')
        rows=json.loads(path.read_text(encoding='utf-8'))
        if not rows or len(rows)>721 or len(rows)!=item['rows']:raise ValueError('CAPTURE_ROWS')
        # Revalidate the normalized values; hashes are integrity checks, not data validation.
        check=[[r[0],r[1],r[2],r[3],r[4],r[4],r[5],0] for r in rows]
        if normalize(check,INTERVALS[item['timeframe']],cutoff)!=rows:raise ValueError('UNCOMMITTED_CAPTURE')
        df=pd.DataFrame(rows,columns=['date','open','high','low','close','volume'])
        df['date']=pd.to_datetime(df['date'],unit='s',utc=True)
        frames[item['pair'],item['timeframe']]=df
    pairs=[p for p in PAIRS if all((p,tf) in frames for tf in INTERVALS)]
    if any(p not in pairs for p in PAIRS[:2]):raise ValueError('BASELINE_FRAMES_MISSING')
    init_db('sqlite://')  # Empty in-memory trades only; native mode sees flat exposure.
    class FrozenProvider:
        runmode=RunMode.DRY_RUN
        def current_whitelist(self):return pairs
        def get_pair_dataframe(self,pair,timeframe,**kwargs):return frames[pair,timeframe].copy(deep=True)
    report={'scope':'OFFLINE_NFI_SIGNAL_COMPARISON_V1','orders':0,'grok_requests':0,
            'analysis_network_requests':0,'capture_reads':manifest['reads'],
            'runtime_versions':{'freqtrade':freqtrade.__version__,'pandas':pd.__version__},
            'native_trade_state':'FLAT_EMPTY_IN_MEMORY_DATABASE','pairs':[],
            'component_source_lines':condition_source_lines(lab/'user_data/strategies'/LOCK['strategy_file']),
            'skipped_pairs':manifest['skipped_pairs'],'source_lock':LOCK,
            'limitations':['Recent snapshot, not a performance backtest or historical universe',
                'Kraken recent-history ceiling leaves few rows after the native startup count',
                'Pair basket is fixed for comparison, not selected by profitability',
                'No fees, fills, exits, win rate, or profitability evaluated',
                'False component masks can reflect missing inputs; inspect missing indicator columns',
                'Not a reconstruction of the previous running bot']}
    with tempfile.TemporaryDirectory(prefix='nfi-signal-study-') as temporary:
        c=json.loads((lab/'user_data/config.json').read_text(encoding='utf-8'))
        c['user_data_dir']=Path(temporary);c['strategy_path']=str(lab/'user_data/strategies')
        c['runmode']=RunMode.DRY_RUN;c['exchange']['pair_whitelist']=pairs
        sys.path.insert(0,c['strategy_path'])
        strategy=StrategyResolver.load_strategy(c);strategy.dp=FrozenProvider()
        module=sys.modules[strategy.__class__.__mro__[1].__module__]
        report['enabled_long_conditions']=sum(bool(v) for v in strategy.long_entry_signal_params.values())
        for pair in pairs:
            stage='INDICATORS'
            try:
                data=strategy.populate_indicators(frames[pair,'5m'].copy(deep=True),{'pair':pair})
                start=strategy.startup_candle_count
                missing=data.iloc[start:].isna().sum()
                stage='ENTRY_CONDITIONS'
                reference=strategy.populate_entry_trend(data.copy(deep=True),{'pair':pair})
                result,counts=trace_signals(strategy,module,data.copy(deep=True),pair,start)
                if not result[['enter_long','enter_short','enter_tag']].equals(reference[['enter_long','enter_short','enter_tag']]):
                    raise ValueError('INSTRUMENTATION_PARITY_FAILED')
                counts['native_signal_parity_verified']=True
                for condition in counts['conditions']:
                    condition['source_mapping_exact']=len(report['component_source_lines'].get(str(condition['signal_id']),[]))==condition['component_count']
                eligible=result.iloc[start:]
                counts.update({'pair':pair,'rows':len(result),'startup_rows':start,
                    'signal_rows':int((eligible['enter_long']==1).sum()),
                    'evaluated_from_utc':str(eligible['date'].iloc[0]) if len(eligible) else None,
                    'evaluated_through_utc':str(eligible['date'].iloc[-1]) if len(eligible) else None,
                    'missing_indicator_columns':{str(k):int(v) for k,v in missing.items() if v>0}})
                report['pairs'].append(counts)
            except Exception as exc:
                report['pairs'].append({'pair':pair,'status':'ANALYSIS_FAILED','stage':stage,'error_type':type(exc).__name__})
    report['status']='COMPLETE' if all('signal_rows' in p for p in report['pairs']) else 'PARTIAL'
    write(output/'signal-comparison.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--capture',action='store_true');mode.add_argument('--analyze',action='store_true')
    p.add_argument('--lab',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    report=capture(a.lab,a.output) if a.capture else analyze(a.lab,a.output)
    print('SIGNAL_STUDY_STATUS='+report['status'])
    if a.analyze:
        for item in report['pairs']:
            print('PAIR='+item['pair']+'; ELIGIBLE_ROWS='+str(item.get('eligible_rows',0))+'; SIGNAL_ROWS='+str(item.get('signal_rows','FAILED')))
        print('UPLOAD_SUMMARY='+str((a.output/'signal-comparison.json').resolve()))
    return 0 if report['status']=='COMPLETE' else 2


if __name__=='__main__':raise SystemExit(main())

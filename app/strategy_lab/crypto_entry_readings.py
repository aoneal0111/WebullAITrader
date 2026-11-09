"""Bounded read-only entry evidence from existing paper journals. Stdlib only."""
import argparse
from collections import Counter
from datetime import UTC,datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import zipfile

PAIRS={'BTC/USD','ETH/USD','SOL/USD','XRP/USD','DOGE/USD','LINK/USD'}
BUDGET=64*1024*1024
MAX_LINE=128*1024


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def read(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>2*1024*1024:raise ValueError('METADATA_BOUND')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def conditions(key,current,previous=None):
    """Report supported clauses; missing indicators remain unknown, not passed."""
    v=current.get('entry_inputs',{});p=current.get('entry_parameters',{});out={}
    def compare(name,a,op,b):
        out[name]=dict(value=a,operator=op,threshold=b,
            passed=None if a is None or b is None else a<b if op=='<' else a>b)
    def times(name,param):return v[name]*p[param] if name in v and param in p else None
    if key=='e0':
        compare('ewo_fast_rsi',v.get('rsi_fast'),'<',p.get('buy_rsi_fast'))
        compare('ewo_ema8_discount',v.get('close'),'<',times('ema_8','buy_ema_low'))
        compare('ewo_strength',v.get('EWO'),'>',p.get('buy_ewo'))
        compare('ewo_ema16_discount',v.get('close'),'<',times('ema_16','buy_ema_high'))
        compare('ewo_rsi',v.get('rsi'),'<',p.get('buy_rsi'))
        prior=None
        if previous:
            difference=(datetime.fromisoformat(current['candle_at'])-datetime.fromisoformat(previous['candle_at'])).total_seconds()
            if difference==300:prior=previous.get('entry_inputs',{}).get('rsi_slow')
        compare('pullback_slow_rsi_falling',v.get('rsi_slow'),'<',prior)
        compare('pullback_fast_rsi',v.get('rsi_fast'),'<',p.get('buy_rsi_fast_32'))
        compare('pullback_rsi_floor',v.get('rsi'),'>',p.get('buy_rsi_32'))
        compare('pullback_sma15_discount',v.get('close'),'<',times('sma_15','buy_sma15_32'))
        compare('pullback_cti',v.get('cti'),'<',p.get('buy_cti_32'))
    elif key=='roc':
        compare('trend_above_sma200',v.get('close'),'>',v.get('sma200'))
        compare('three_hour_drop',v.get('roc3'),'<',-.08)
        compare('nonempty_volume',v.get('volume'),'>',0)
    elif key=='nasos':
        compare('positive_ewo_branch',v.get('EWO'),'>',p.get('ewo_high'))
        compare('negative_ewo_branch',v.get('EWO'),'<',p.get('ewo_low'))
        compare('rsi',v.get('rsi'),'<',p.get('rsi_buy'))
        out['coverage']='Partial clauses only; dynamic MA discount and other source branches remain unevaluated'
    return out


def collect_worker(work,key):
    work=Path(work);engine=read(work/'engine-status.json');pointer=read(work/'recording-session.json')
    name=str(pointer['session']).replace('\\','/').rstrip('/').split('/')[-1]
    if not re.fullmatch(r'recording-\d{8}T\d{6}Z-[0-9a-f]{32}',name):raise ValueError('RECORDING_POINTER')
    root=work/name
    if root.is_symlink():raise ValueError('RECORDING_LINK')
    if engine.get('recording_id') not in (None,name):raise ValueError('RECORDING_CHANGED_RETRY')
    anchor=read(root/'journal-status.json');baseline=anchor['baseline_sha256']
    config=work/('resolved-config.json' if key=='nfi' else 'config.json')
    if sha256(config.read_bytes()).hexdigest()!=baseline:raise ValueError('SOURCE_CONFIG_MISMATCH')
    limit=anchor['counts']['written'];signals={};candles={};scanned=0;verified=0
    partial=0;revisions=0
    for path in sorted(root.glob('events-*.jsonl'),reverse=True):
        if path.is_symlink():raise ValueError('JOURNAL_LINK')
        size=path.stat().st_size;take=min(size,BUDGET-scanned)
        if take<=0:break
        with path.open('rb') as f:f.seek(size-take);raw=f.read(take)
        scanned+=len(raw);lines=raw.splitlines(keepends=True)
        if size>take:lines=lines[1:]
        for line in reversed(lines):
            if not line.endswith(b'\n'):partial+=1;continue
            if len(line)>MAX_LINE:raise ValueError('OVERSIZE_JOURNAL_ROW')
            row=json.loads(line)
            if row['sequence']>limit:continue
            claimed=row.pop('sha256')
            if row['baseline_sha256']!=baseline or sha256(canonical(row)).hexdigest()!=claimed:
                raise ValueError('ROW_HASH_MISMATCH')
            verified+=1;payload=row['payload'];kind=row['kind']
            if kind.endswith('_NATIVE_SIGNAL'):
                pair=payload['pair'];source=payload.get('source_readiness',{})
                history=signals.setdefault(pair,[])
                if len(history)<2 and source.get('candle_at') not in {r.get('candle_at') for r in history}:
                    history.append(source)
            elif kind.endswith('_NATIVE_CANDLES') and payload.get('pair') in PAIRS:
                if payload.get('timeframe') not in ('5m','15m','1h','4h','1d'):continue
                index=(payload['pair'],payload['timeframe']);bucket=candles.setdefault(index,{})
                for bar in reversed(payload['rows']):
                    at=bar['date']
                    if at in bucket:
                        if bucket[at]!=bar:revisions+=1
                    elif len(bucket)<720:bucket[at]=bar
    latest={}
    for pair,history in sorted(signals.items()):
        if history:latest[pair]=dict(source=history[0],conditions=conditions(key,history[0],history[1] if len(history)>1 else None))
    # NFI exposes complete per-pair source activity flags in its existing status.
    nfi=engine.get('source_readiness',{}).get('by_pair',{}) if key=='nfi' else {}
    result=dict(engine={k:engine.get(k) for k in ('state','updated_at','fresh_ready_pairs','pause_new_entries','capture_error')},
        recording_id=name,checkpoint_at=anchor['updated_at'],config_sha256=baseline,
        observed_strategy_sha256={p.name:sha256(p.read_bytes()).hexdigest() for p in sorted((work/'strategies').glob('*.py'))},
        journal_bytes_scanned=scanned,rows_hash_verified=verified,partial_tail_lines_ignored=partial,
        scan_bound_reached=scanned>=BUDGET,full_hash_chain_verified=False,
        latest_signals=latest,nfi_pair_flags=nfi,
        nfi_empty_candle_pairs={p:v for p,v in nfi.items() if v.get('state')=='SOURCE_EMPTY_CANDLE_LIMIT'},
        retained_candle_revisions=revisions,
        native_candle_counts={pair+'|'+tf:len(b) for (pair,tf),b in candles.items()})
    data={pair.replace('/','_')+'-'+tf+'.json':sorted(b.values(),key=lambda x:x['date']) for (pair,tf),b in candles.items()}
    return result,data


def collect(fleet,addon,output):
    fleet,addon,output=map(Path,(fleet,addon,output));manifest=read(fleet/'fleet.json')
    if manifest.get('live_trading_enabled') is not False:raise ValueError('PAPER_FLEET_REQUIRED')
    works={key:fleet/key for key in ('e0','nasos','bb','crsi','wave')}
    works['nfi']=Path(manifest['nfi_work']);works['roc']=addon/'crypto'
    output.mkdir(parents=True,exist_ok=False)
    report=dict(scope='READ_ONLY_CURRENT_CRYPTO_ENTRY_READINGS_V1',captured_at=datetime.now(UTC).isoformat(),
        profiles={},collector_orders=0,collector_network_requests=0,workers_restarted=False,source_files_modified=False,
        limitations=['Bounded live-journal tails; individual hashes checked, not a complete chain audit',
            'Missing conditions remain unknown; recent signal observations are not win-rate samples',
            'Native candles may contain source zero-volume normalization; no missing rows invented',
            'Latest recorded candle revisions are diagnostic inputs, not a causal historical execution replay'])
    archive=output/'crypto-entry-readings.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for key,work in works.items():
            try:
                result,data=collect_worker(work,key);report['profiles'][key]=result
                for name,rows in data.items():z.writestr(key+'/candles/'+name,json.dumps(rows,separators=(',',':')))
            except Exception as exc:report['profiles'][key]=dict(error=type(exc).__name__,detail=str(exc))
        raw=json.dumps(report,indent=2,allow_nan=False)
        (output/'entry-readings.json').write_text(raw,encoding='utf-8');z.writestr('entry-readings.json',raw)
    if archive.stat().st_size>4*1024*1024:
        archive.unlink();raise ValueError('ARCHIVE_SIZE_BOUND; use entry-readings.json')
    print('UPLOAD_FILE='+str(archive));print('READINGS_JSON='+str(output/'entry-readings.json'))
    print('READ_ONLY; NO_REQUESTS_OR_RESTARTS; NO_PARAMETERS_CHANGED')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--fleet',required=True)
    p.add_argument('--addon',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();collect(a.fleet,a.addon,a.output)

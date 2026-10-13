"""Bounded offline saved-bar/quote assessment. No orders, AI or performance totals."""
import argparse
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
import json
import time

from app.asset_modules.contract_worker_codec import _decimal, _symbol, _time
from app.asset_modules.crypto_history_adapter import adapt
from app.asset_modules.crypto_signals import POLICY, CryptoSignalSettings
from app.asset_modules.crypto_worker import CryptoSignalWorker
from app.asset_modules.engine_worker import WorkerState
from app.crypto_research.bar_capture import read_bars
from app.crypto_research.models import CryptoObservation, CryptoPair

MAX_RECORD_BYTES = 16384


def synthetic():
    from tempfile import TemporaryDirectory
    from datetime import timedelta
    from app.crypto_research.bar_capture import CryptoBarCapture
    from app.crypto_research.evidence import CryptoBarInterval, normalize_crypto_rows
    now=datetime(2026,10,13,15,tzinfo=UTC)
    pair=CryptoPair('BTC','USD','BTCUSD')
    rows=tuple({'timestamp':(now-timedelta(minutes=22-i,seconds=1)).isoformat(),
        'open':'100' if i<21 else '101','high':'100.2' if i<21 else '101.2',
        'low':'98' if i<21 else '100','close':'100' if i<21 else '101'} for i in range(22))
    bars=normalize_crypto_rows(pair,rows,interval=CryptoBarInterval.M1,observed_at=now,decision_cutoff=now).bars
    def quote(at):
        return {'asset_type':'CRYPTO','canonical_symbol':'BTC/USD','provider_symbol':'BTCUSD',
            'base_asset':'BTC','quote_asset':'USD','timestamp':at.isoformat(),'decision_cutoff':at.isoformat(),
            'price':'101','bid':'101','ask':'101.1','research_only':True,'execution_authorized':False}
    with TemporaryDirectory(prefix='atlas-crypto-replay-') as directory:
        path=Path(directory)/'bars.db';quotes=Path(directory)/'quotes.jsonl'
        CryptoBarCapture(path).append_many(bars,captured_at=now)
        records=(quote(now-timedelta(seconds=1)),quote(now),quote(now),quote(now+timedelta(seconds=1)))
        quotes.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
        report=replay(path,quotes)
        passed=(report['worker_assessments']==2 and report['unique_signal_ids']==1
            and report['status_counts'].get('NO_COMPLETED_PROVIDER_BARS')==1
            and report['status_counts'].get('DUPLICATE_QUOTE')==1)
        print(json.dumps({'scope':'SYNTHETIC_RECORDED_REPLAY_NOT_REAL_DATA_OR_PERFORMANCE',
            'passed':passed,'comparison':report},indent=2))
        return 0 if passed else 1


def _pairs(values):
    result={}
    for key,value in values:
        if key in result:raise ValueError('Duplicate quote record field')
        result[key]=value
    return result


def _record(raw):
    r=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Non-finite JSON')))
    if not isinstance(r,dict) or r.get('asset_type')!='CRYPTO':raise ValueError('Crypto record required')
    symbol=_symbol(r['canonical_symbol'])
    pair=CryptoPair(r['base_asset'],r['quote_asset'],_symbol(r['provider_symbol']))
    if pair.canonical_symbol!=symbol or pair.quote_asset!='USD':raise ValueError('Owned spot USD quote required')
    source,cutoff=_time(r['timestamp']),_time(r['decision_cutoff'])
    if source>cutoff:raise ValueError('Quote exceeds declared cutoff')
    if r.get('execution_authorized') is not False or r.get('research_only') is not True:
        raise ValueError('Research-only quote provenance required')
    bid=None if r['bid'] is None else _decimal(r['bid'])
    ask=None if r['ask'] is None else _decimal(r['ask'])
    q=CryptoObservation(pair,source,_decimal(r['price']),bid,ask,None)
    return q,cutoff


def replay(bar_path,quote_path,*,symbol='BTC/USD',max_scan=1000,max_assessments=100,offset=0):
    if (type(max_scan) is not int or not 1<=max_scan<=10000
            or type(max_assessments) is not int or not 1<=max_assessments<=100
            or type(offset) is not int or offset<0):
        raise ValueError('Bounded replay scan, assessments and byte offset required')
    symbol=_symbol(symbol)
    if not symbol.endswith('/USD') or symbol.count('/')!=1:raise ValueError('Canonical spot USD symbol required')
    bar_path,quote_path=Path(bar_path).resolve(),Path(quote_path).resolve()
    if bar_path==quote_path:raise ValueError('Distinct saved bars and quote log required')
    if not bar_path.is_file() or not quote_path.is_file():raise FileNotFoundError('Replay inputs missing')
    if offset>quote_path.stat().st_size:raise ValueError('Byte offset exceeds quote log')
    statuses=Counter();samples=[];signal_ids=set();seen=set()
    worker=None;scanned=assessed=proposed=0;last_cutoff=None
    def wait():
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            reply=worker.poll()
            if reply:return reply
            if worker.state==WorkerState.FAILED:raise RuntimeError('Crypto replay worker unavailable')
            time.sleep(.01)
        raise RuntimeError('Crypto replay worker timeout')
    try:
        with quote_path.open('rb') as handle:
            if offset:
                handle.seek(offset-1)
                if handle.read(1)!=b'\n':raise ValueError('Byte offset must follow a complete record')
            while scanned<max_scan and assessed<max_assessments:
                raw=handle.readline(MAX_RECORD_BYTES+1)
                if not raw:break
                if len(raw)>MAX_RECORD_BYTES:raise ValueError('Quote record exceeds byte bound')
                if not raw.endswith(b'\n'):
                    # A live writer may not have committed this trailing record yet.
                    handle.seek(-len(raw),1);statuses['INCOMPLETE_TRAILING_RECORD']+=1;break
                scanned+=1
                try:q,cutoff=_record(raw)
                except (ValueError,KeyError,TypeError,ArithmeticError,UnicodeError,RecursionError):
                    statuses['INVALID_QUOTE_RECORD']+=1;continue
                if q.pair.canonical_symbol!=symbol:
                    statuses['OTHER_SYMBOL']+=1;continue
                if last_cutoff is not None and cutoff<last_cutoff:
                    statuses['OUT_OF_ORDER_CUTOFF']+=1;continue
                last_cutoff=cutoff
                signature=(q.pair.provider_symbol,q.timestamp,cutoff,q.price,q.bid,q.ask)
                if signature in seen:
                    statuses['DUPLICATE_QUOTE']+=1;continue
                seen.add(signature)
                # Eligibility uses first durable bar availability at this quote's
                # declared decision cutoff, never the replay machine's current time.
                bars=read_bars(bar_path,symbol,as_of=cutoff)
                frame=adapt(symbol,bars,q,now=cutoff)
                if frame.status!='READY':
                    statuses[frame.status]+=1;continue
                if worker is None:
                    worker=CryptoSignalWorker(timeout_seconds=10);worker.start()
                    if wait()['kind']!='READY':raise RuntimeError('Crypto replay worker not ready')
                worker.assess(frame.history,frame.quote,cutoff,CryptoSignalSettings())
                reply=wait()
                if reply['kind']!='ASSESSED':raise RuntimeError('Unexpected replay completion')
                result=reply['payload'];assessed+=1;statuses[result['reason']]+=1
                if result['proposal'] is not None:
                    proposed+=1;signal_ids.add(result['proposal']['id'])
                if len(samples)<20:
                    samples.append({'source_at':q.timestamp.isoformat(),'decision_cutoff':cutoff.isoformat(),
                        'worker_bar_count':frame.worker_bar_count,'assessment':result['reason'],
                        'signal_id':None if result['proposal'] is None else result['proposal']['id']})
            next_offset=handle.tell()
            at_eof=next_offset>=quote_path.stat().st_size
        return {'version':'CRYPTO_RECORDED_SIGNAL_ASSESSMENT_V1',
            'scope':'OFFLINE_READ_ONLY_SIGNAL_ASSESSMENT_NO_ORDERS_OR_PNL','policy':POLICY,
            'symbol':symbol,'scanned_rows':scanned,'worker_assessments':assessed,'proposed_assessments':proposed,
            'unique_signal_ids':len(signal_ids),'status_counts':dict(statuses),'samples':samples,
            'start_offset':offset,'next_offset':next_offset,'at_eof':at_eof,'worker_started':worker is not None,
            'timestamp_basis':'DECLARED_QUOTE_DECISION_CUTOFF_NOT_QUOTE_FILE_WRITE_TIME',
            'selection':'BOUNDED_FILE_ORDER_SAMPLE_NOT_REPRESENTATIVE_PERFORMANCE',
            'execution_enabled':False,'orders':0,'network_requests':0,'promotion':'NONE'}
    finally:
        if worker is not None:worker.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic',action='store_true')
    parser.add_argument('--bars',type=Path)
    parser.add_argument('--quotes',type=Path)
    parser.add_argument('--symbol',default='BTC/USD')
    parser.add_argument('--max-scan',type=int,default=1000)
    parser.add_argument('--max-assessments',type=int,default=100)
    parser.add_argument('--offset',type=int,default=0)
    args=parser.parse_args(argv)
    if args.synthetic:return synthetic()
    from dotenv import load_dotenv
    from app.configuration.loader import load_configuration
    load_dotenv(override=False)
    config=load_configuration()
    quotes=args.quotes or config.crypto_discovery_path
    bars=args.bars or config.crypto_discovery_path.with_name('crypto-completed-bars.sqlite3')
    try:
        report=replay(bars,quotes,symbol=args.symbol,max_scan=args.max_scan,max_assessments=args.max_assessments,offset=args.offset)
    except FileNotFoundError:
        print(json.dumps({'scope':'OFFLINE_READ_ONLY_SIGNAL_ASSESSMENT_NO_ORDERS_OR_PNL',
            'status':'MISSING_INPUTS','bars_exists':bars.is_file(),'quotes_exists':quotes.is_file(),
            'orders':0,'network_requests':0,'promotion':'NONE'},indent=2));return 0
    except Exception as error:
        print(json.dumps({'scope':'OFFLINE_READ_ONLY_SIGNAL_ASSESSMENT_NO_ORDERS_OR_PNL',
            'status':'REPLAY_UNAVAILABLE','error_class':type(error).__name__,
            'orders':0,'network_requests':0,'promotion':'NONE'},indent=2));return 1
    print(json.dumps(report,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())

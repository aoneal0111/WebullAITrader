"""Offline synthetic capture check or read-only existing capture window report."""
import argparse
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.crypto_research.bar_capture import CryptoBarCapture, read_bars
from app.crypto_research.evidence import CryptoBarInterval, normalize_crypto_rows
from app.crypto_research.models import CryptoPair, CryptoObservation


def synthetic():
    from app.asset_modules.crypto_history_adapter import adapt
    now=datetime(2026,10,13,15,tzinfo=UTC)
    pair=CryptoPair('BTC','USD','BTCUSD')
    rows=tuple({'timestamp':(now-timedelta(minutes=22-i,seconds=1)).isoformat(),
        'open':'100' if i<21 else '101','high':'100.2' if i<21 else '101.2',
        'low':'98' if i<21 else '100','close':'100' if i<21 else '101'} for i in range(22))
    bars=normalize_crypto_rows(pair,rows,interval=CryptoBarInterval.M1,observed_at=now,decision_cutoff=now).bars
    with TemporaryDirectory(prefix='atlas-crypto-bars-') as directory:
        path=Path(directory)/'provider-bars.db'
        stored=CryptoBarCapture(path).append_many(bars,captured_at=now)
        later=tuple(replace(b,observed_at=now+timedelta(seconds=1),decision_cutoff=now+timedelta(seconds=1)) for b in bars)
        duplicate=CryptoBarCapture(path).append_many(later,captured_at=now+timedelta(seconds=1))
        recovered=read_bars(path,'BTC/USD',as_of=now)
        unavailable=read_bars(path,'BTC/USD',as_of=now-timedelta(microseconds=1))
        quote=CryptoObservation(pair,now,D(101),D(101),D('101.1'),None)
        frame=adapt('BTC/USD',recovered,quote,now=now)
        passed=(stored.stored==22 and duplicate.duplicates==22 and recovered==bars
            and not unavailable and frame.status=='READY')
        print(json.dumps({'scope':'SYNTHETIC_DURABLE_BAR_CAPTURE_NOT_REAL_DATA_VALIDATION',
            'passed':passed,'stored_bars':stored.stored,'duplicate_bars':duplicate.duplicates,
            'recovered_bars':len(recovered),'bars_available_before_capture':len(unavailable),
            'adapter_status':frame.status,'network_requests':0,'orders':0},indent=2))
        return 0 if passed else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic',action='store_true')
    parser.add_argument('--path',type=Path)
    parser.add_argument('--symbol',default='BTC/USD')
    parser.add_argument('--as-of')
    args=parser.parse_args(argv)
    if args.synthetic:return synthetic()
    from dotenv import load_dotenv
    from app.configuration.loader import load_configuration
    load_dotenv(override=False)
    path=args.path or load_configuration().crypto_discovery_path.with_name('crypto-completed-bars.sqlite3')
    report={'scope':'READ_ONLY_COMPLETED_BAR_CAPTURE_NO_ORDERS','path':str(path.resolve()),
        'symbol':args.symbol,'network_requests':0,'orders':0,'quote_validation':'NOT_PERFORMED'}
    if not path.is_file():
        print(json.dumps({**report,'status':'MISSING_CAPTURE','bar_count':0},indent=2));return 0
    try:
        now=datetime.now(UTC) if args.as_of is None else datetime.fromisoformat(args.as_of)
        bars=read_bars(path,args.symbol,as_of=now)
        result={**report,'status':'BAR_WINDOW_AVAILABLE' if bars else 'NO_ELIGIBLE_BARS',
            'bar_count':len(bars),'as_of':now.isoformat(),
            'latest_bar_close':None if not bars else bars[-1].closed_at.isoformat()}
    except Exception as error:
        print(json.dumps({**report,'status':'CAPTURE_UNAVAILABLE','error_class':type(error).__name__},indent=2));return 1
    print(json.dumps(result,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())

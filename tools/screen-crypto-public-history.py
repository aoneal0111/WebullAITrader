"""Bounded public Coinbase candles -> frozen Atlas research screen. No accounts."""
import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.strategy_research.crypto_screening import Bar, epoch, screen, validate


def capture(output):
    output.mkdir(parents=True, exist_ok=False)
    start, end, step = epoch('2022-01-01'), epoch('2026-10-23'), 21600
    data, manifest = {}, []
    for symbol in ('BTC-USD', 'ETH-USD'):
        rows = {}
        for index, left in enumerate(range(start, end, 299*step)):
            right = min(left+299*step, end)
            fmt = lambda t: datetime.fromtimestamp(t, UTC).isoformat().replace('+00:00', 'Z')
            url = (f'https://api.exchange.coinbase.com/products/{symbol}/candles'
                   f'?granularity={step}&start={fmt(left)}&end={fmt(right)}')
            raw = None
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers={'User-Agent':'AtlasOfflineResearch/1.0'})
                    with urllib.request.urlopen(req, timeout=25) as response:
                        raw = response.read(1_000_001)
                    if len(raw)>1_000_000:
                        raise ValueError('response exceeds bound')
                    break
                except (OSError, TimeoutError):
                    if attempt==2: raise
                    time.sleep(1+attempt)
            path = output/f'{symbol}-{index:03d}.json'
            path.write_bytes(raw)
            manifest.append(dict(file=path.name, sha256=hashlib.sha256(raw).hexdigest(), url=url))
            values = json.loads(raw)
            if not isinstance(values,list) or len(values)>300:
                raise ValueError('unsupported candle response')
            for value in values:
                if len(value)!=6 or type(value[0]) is not int:
                    raise ValueError('invalid candle schema')
                t, low, high, opening, closing, volume = value
                if not left<=t<right: continue
                b = Bar(t,float(opening),float(high),float(low),float(closing),float(volume))
                if t in rows and rows[t]!=b: raise ValueError('conflicting candle')
                rows[t]=b
            time.sleep(.35)
        bars = tuple(rows[t] for t in sorted(rows))
        validate(bars, step)
        if len(bars)!=(end-start)//step or bars[0].start!=start or bars[-1].start+step!=end:
            raise ValueError('incomplete public history; screen aborted')
        data[symbol] = bars
        (output/f'{symbol}-bars.json').write_text(json.dumps([asdict(b) for b in bars]))
        print(f'{symbol}: validated {len(bars)} contiguous 6h candles', flush=True)
    (output/'manifest.json').write_text(json.dumps(dict(captured_at=datetime.now(UTC).isoformat(),
        source='Coinbase Exchange public REST, not Webull execution data', responses=manifest),indent=2))
    result = screen(data)
    (output/'screening.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    print(json.dumps(result['gates'],indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    capture(parser.parse_args().output)

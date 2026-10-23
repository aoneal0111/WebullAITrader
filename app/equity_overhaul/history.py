"""Bounded market-data-only Alpaca minute capture for the frozen temporal plan.

Credentials are supplied locally by the operator. No trading endpoint, account
lookup, automatic subscription, feed fallback or order service is involved.
"""
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from app.strategy_research.candidates import Bar, validate_bars
from .validation import PLAN, SYMBOLS, frozen_plan, schedule

ENDPOINT = 'https://data.alpaca.markets/v2/stocks/bars'
MAX_PAGES = 400
MAX_PAGE_BYTES = 4_000_000


class CaptureError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_page(params, credentials):
    request = urllib.request.Request(ENDPOINT+'?'+urllib.parse.urlencode(params), headers={
        'APCA-API-KEY-ID': credentials[0], 'APCA-API-SECRET-KEY': credentials[1],
        'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(MAX_PAGE_BYTES+1)
    except urllib.error.HTTPError as exc:
        # Neither provider bodies nor headers, URLs or credentials are exported.
        raise CaptureError(f'HTTP_{exc.code}') from None
    except OSError:
        raise CaptureError('NETWORK_ERROR') from None
    if len(raw) > MAX_PAGE_BYTES:
        raise CaptureError('RESPONSE_BOUND_EXCEEDED')
    return raw


def capture(root, plan_document, credentials, *, fetch=request_page, pause=time.sleep, progress=print):
    if plan_document != frozen_plan():
        raise CaptureError('PLAN_OR_SOURCE_MISMATCH')
    if len(credentials) != 2 or any(not isinstance(x, str) or not x.strip() for x in credentials):
        raise CaptureError('LOCAL_ALPACA_DATA_CREDENTIALS_REQUIRED')
    start = date.fromisoformat(PLAN['windows'][0]['start'])-timedelta(days=PLAN['warmup_calendar_days'])
    end = date.fromisoformat(PLAN['windows'][-1]['end_exclusive'])
    days = schedule(start, end)
    root = Path(root); root.mkdir(parents=True, exist_ok=False)
    manifest = {'source': 'ALPACA_SIP_MINUTE_RAW', 'adjustment': 'raw', 'feed': 'sip',
                'selection': 'FIXED_LIQUID_SAMPLE_NOT_POINT_IN_TIME_MOVERS',
                'captured_at': datetime.now(UTC).isoformat(), 'plan_document': plan_document,
                'responses': [], 'normalized_inputs': [], 'coverage': [],
                'execution_authorized': False, 'status': 'PARTIAL', 'error': None,
                'limits': {'max_pages': MAX_PAGES, 'max_page_bytes': MAX_PAGE_BYTES},
                'notes': ['Historical backfill is not live availability evidence',
                          'No executable quotes or verified Webull costs',
                          'No subscription purchase or IEX fallback']}
    rows = {symbol: {} for symbol in SYMBOLS}; tokens = set(); token = None
    try:
        for index in range(MAX_PAGES):
            params = {'symbols': ','.join(SYMBOLS), 'timeframe': '1Min',
                      'start': start.isoformat()+'T00:00:00Z',
                      # API end is inclusive; request the last whole second before our exclusive date.
                      'end': (datetime.combine(end, datetime.min.time(), UTC)-timedelta(seconds=1)).isoformat(),
                      'feed': 'sip', 'adjustment': 'raw', 'asof': '-', 'sort': 'asc', 'limit': 10000}
            if token is not None: params['page_token'] = token
            if index: pause(.35)
            raw = fetch(params, credentials)
            if not isinstance(raw, bytes) or len(raw) > MAX_PAGE_BYTES:
                raise CaptureError('RESPONSE_BOUND_EXCEEDED')
            payload = json.loads(raw, parse_float=D)
            batch = payload.get('bars')
            if not isinstance(batch, dict) or set(batch)-set(SYMBOLS):
                raise CaptureError('UNEXPECTED_SYMBOLS_OR_SHAPE')
            if sum(len(items) for items in batch.values()) > 10000:
                raise CaptureError('ROW_BOUND_EXCEEDED')
            name = f'page-{index:04d}.json'; (root/name).write_bytes(raw)
            manifest['responses'].append({'file': name, 'sha256': sha256(raw).hexdigest()})
            for symbol, items in batch.items():
                for item in items:
                    stamp = datetime.fromisoformat(item['t'].replace('Z', '+00:00'))
                    if stamp.utcoffset() is None or stamp.second or stamp.microsecond:
                        raise CaptureError('UNALIGNED_OR_UNDATED_BAR')
                    day = stamp.astimezone(ZoneInfo('America/New_York')).date()
                    if not start <= day < end: raise CaptureError('BAR_OUTSIDE_REQUEST')
                    if day not in days or not days[day][0] <= stamp < days[day][1]: continue
                    numbers = tuple(D(str(item[k])) for k in ('o', 'h', 'l', 'c', 'v'))
                    o, h, l, c, v = numbers
                    if (not all(x.is_finite() for x in numbers) or v < 0 or
                            not 0 < l <= min(o, c) <= max(o, c) <= h):
                        raise CaptureError('INVALID_OHLCV')
                    if stamp in rows[symbol] and rows[symbol][stamp] != numbers:
                        raise CaptureError('CONFLICTING_DUPLICATE')
                    rows[symbol][stamp] = numbers
                    if len(rows[symbol]) > 400_000: raise CaptureError('SYMBOL_ROW_BOUND_EXCEEDED')
            progress(f'CAPTURE_PAGES={index+1}', flush=True)
            token = payload.get('next_page_token')
            if token is None: break
            if not isinstance(token, str) or not token or len(token) > 4096 or token in tokens:
                raise CaptureError('INVALID_OR_REPEATED_PAGE_TOKEN')
            tokens.add(token)
        else:
            raise CaptureError('PAGE_LIMIT_EXCEEDED')
        for symbol in SYMBOLS:
            bars = []
            for day, (opened, closed) in days.items():
                wanted = [opened+timedelta(minutes=i) for i in range(int((closed-opened).total_seconds()/60))]
                missing = [stamp for stamp in wanted if stamp not in rows[symbol]]
                manifest['coverage'].append({'symbol': symbol, 'date': day.isoformat(),
                    'expected_minutes': len(wanted), 'missing_minutes': len(missing),
                    'status': 'EXCLUDED_INCOMPLETE' if missing else 'COMPLETE'})
                if missing: continue
                bars.extend(Bar(symbol, stamp, stamp+timedelta(minutes=1), *rows[symbol][stamp], closed)
                            for stamp in wanted)
            if not bars: continue
            validate_bars(tuple(bars), 'minute')
            raw = json.dumps([asdict(b) for b in bars], default=str).encode()
            if len(raw) > 80_000_000: raise CaptureError('NORMALIZED_FILE_BOUND_EXCEEDED')
            name = f'{symbol}-normalized.json'; (root/name).write_bytes(raw)
            manifest['normalized_inputs'].append({'file': name, 'sha256': sha256(raw).hexdigest()})
        manifest['status'] = ('COMPLETE' if all(x['status'] == 'COMPLETE' for x in manifest['coverage'])
                              else 'INCOMPLETE_SESSION_COVERAGE')
    except Exception as exc:
        manifest['error'] = str(exc) if isinstance(exc, CaptureError) else 'MALFORMED_PROVIDER_DATA'
    finally:
        (root/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest

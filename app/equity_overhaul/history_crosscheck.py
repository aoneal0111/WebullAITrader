"""Second-vendor daily diagnostics. Never certifies minute fills or grants orders."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
from itertools import groupby
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from .validation import load_dataset, PLAN, fingerprint, SYMBOLS
from .local_audit import read_json, digest, source_matches

EASTERN = ZoneInfo('America/New_York')
MAX_RESPONSE = 2_000_000
PRICE_BPS = D(10)
ABS_PRICE = D('.02')


class CheckError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_daily(symbol, start, end):
    """One unauthenticated public request per symbol. No retries/login fallback."""
    if symbol not in SYMBOLS:
        raise CheckError('UNEXPECTED_SYMBOL')
    params = urllib.parse.urlencode(dict(period1=int(start.timestamp()), period2=int(end.timestamp()),
        interval='1d', events='div,splits', includeAdjustedClose='true'))
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + symbol + '?' + params
    request = urllib.request.Request(url, headers={'User-Agent': 'AtlasHistoryDiagnostic/1.0', 'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        raise CheckError('HTTP_' + str(exc.code)) from None
    except OSError:
        raise CheckError('NETWORK_ERROR') from None
    if len(raw) > MAX_RESPONSE:
        raise CheckError('RESPONSE_BOUND_EXCEEDED')
    return raw


def parse_daily(raw, symbol):
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE:
        raise CheckError('RESPONSE_BOUND_EXCEEDED')
    payload = json.loads(raw, parse_float=D)
    chart = payload['chart']
    if chart.get('error') or len(chart['result']) != 1:
        raise CheckError('PROVIDER_ERROR_OR_SHAPE')
    result = chart['result'][0]; meta = result['meta']
    if meta.get('symbol') != symbol or meta.get('currency') != 'USD' or meta.get('exchangeTimezoneName') != 'America/New_York':
        raise CheckError('PROVIDER_IDENTITY_MISMATCH')
    stamps = result['timestamp']; quotes = result['indicators']['quote']
    if len(stamps) > 1000 or len(quotes) != 1 or len(set(stamps)) != len(stamps):
        raise CheckError('PROVIDER_ROW_BOUND_OR_DUPLICATE')
    q = quotes[0]
    if any(len(q[k]) != len(stamps) for k in ('open','high','low','close','volume')):
        raise CheckError('PROVIDER_ARRAY_LENGTH')
    rows = {}; nulls = 0
    for i, stamp in enumerate(stamps):
        day = datetime.fromtimestamp(stamp, UTC).astimezone(EASTERN).date()
        if day in rows: raise CheckError('PROVIDER_DUPLICATE_DATE')
        if any(q[k][i] is None for k in ('open','high','low','close','volume')):
            nulls += 1; continue
        values = {k:D(str(q[k][i])) for k in ('open','high','low','close','volume')}
        if (not all(v.is_finite() for v in values.values()) or values['volume'] < 0 or
                not 0 < values['low'] <= min(values['open'],values['close']) <= max(values['open'],values['close']) <= values['high']):
            raise CheckError('INVALID_PROVIDER_OHLCV')
        rows[day] = values
    events = result.get('events', {})
    # Yahoo price adjustment conventions cannot be assumed identical to raw SIP.
    # Any returned split makes price-level comparison inconclusive; never silently rescale.
    splits = len(events.get('splits', {})); dividends = len(events.get('dividends', {}))
    return rows, dict(split_events=splits, dividend_events=dividends, null_rows=nulls)


def daily_from_minutes(bars):
    result = {}
    for _, grouped in groupby(bars, key=lambda b:b.session_end):
        items = tuple(grouped); day = items[0].opened.astimezone(EASTERN).date()
        result[day] = dict(open=items[0].open, high=max(b.high for b in items),
            low=min(b.low for b in items), close=items[-1].close,
            volume=sum((b.volume for b in items), D(0)))
    return result


def compare_daily(local, other, events):
    dates = sorted(local); missing = sorted(set(local)-set(other)); common = sorted(set(local)&set(other))
    mismatches = []; deviations = {k:[] for k in ('open','high','low','close')}
    volume_gaps = []
    for day in common:
        fields = []
        for k in deviations:
            a,b = local[day][k],other[day][k]; delta = abs(a-b)
            deviations[k].append(delta/a*10000)
            if delta > max(ABS_PRICE, a*PRICE_BPS/10000): fields.append(k)
        if fields and len(mismatches)<10:
            mismatches.append(dict(date=day.isoformat(), fields=fields))
        if local[day]['volume']:
            volume_gaps.append(abs(local[day]['volume']-other[day]['volume'])/local[day]['volume'])
    mismatch_count = sum(any(abs(local[d][k]-other[d][k]) > max(ABS_PRICE,local[d][k]*PRICE_BPS/10000)
        for k in deviations) for d in common)
    status = ('INCONCLUSIVE_SPLIT_ADJUSTMENT' if events['split_events'] else
              'MISSING_SECOND_SOURCE_DATES' if missing else
              'PRICE_DIFFERENCES_REQUIRE_REVIEW' if mismatch_count else 'DAILY_PRICES_WITHIN_DIAGNOSTIC_TOLERANCE')
    return dict(status=status, local_dates=len(dates), compared_dates=len(common),
        first_date=dates[0].isoformat(), last_date=dates[-1].isoformat(),
        missing_dates=len(missing), missing_examples=[d.isoformat() for d in missing[:10]],
        price_difference_dates=mismatch_count, difference_examples=mismatches,
        max_price_difference_bps={k:str(max(v)) if v else None for k,v in deviations.items()},
        median_volume_relative_difference=str(sorted(volume_gaps)[len(volume_gaps)//2]) if volume_gaps else None,
        corporate_actions=events)


def run_crosscheck(history, report_path, capture_directory, *, fetch=fetch_daily, progress=print, now=None):
    history = Path(history); report = read_json(report_path,40_000_000)
    original = report['plan_document']; manifest = read_json(history/'manifest.json',2_000_000)
    if (report.get('scope') != 'OFFLINE_EQUITY_TEMPORAL_SCREEN' or original['plan'] != PLAN or
            original['plan_sha256'] != fingerprint(PLAN) or manifest['plan_document'] != original or
            not source_matches(original['source_hashes'])):
        raise CheckError('ORIGINAL_EVIDENCE_IDENTITY_MISMATCH')
    data, provenance = load_dataset(history)
    if (set(data) != set(SYMBOLS) or provenance['manifest_sha256'] != report['data']['manifest_sha256'] or
            provenance['normalized_inputs'] != report['data']['normalized_inputs']):
        raise CheckError('CAPTURED_HISTORY_REPORT_MISMATCH')
    root = Path(capture_directory); root.mkdir(parents=True, exist_ok=False)
    now = now or datetime.now(UTC)
    if now.utcoffset() is None: raise CheckError('AWARE_CAPTURE_TIME_REQUIRED')
    now = now.astimezone(UTC)
    # Ask through today's UTC boundary to include later split events affecting old prices.
    end = datetime.combine(now.date(), datetime.min.time(), UTC)
    cells = []; responses = []
    for symbol in SYMBOLS:
        local = daily_from_minutes(data[symbol])
        start = datetime.combine(min(local), datetime.min.time(), EASTERN).astimezone(UTC)
        if max(local) >= (now-timedelta(days=1)).date():
            raise CheckError('ONLY_COMPLETED_HISTORICAL_DATES_SUPPORTED')
        try:
            raw = fetch(symbol,start,end)
            if not isinstance(raw,bytes) or len(raw)>MAX_RESPONSE: raise CheckError('RESPONSE_BOUND_EXCEEDED')
            name = symbol+'-daily.json'; (root/name).write_bytes(raw)
            responses.append(dict(symbol=symbol,file=name,sha256=sha256(raw).hexdigest()))
            other,events = parse_daily(raw,symbol)
            cell = compare_daily(local,other,events)
        except (ValueError,KeyError,TypeError,OSError,ArithmeticError) as exc:
            cell = dict(status='SECOND_SOURCE_UNAVAILABLE', error=str(exc) if isinstance(exc,CheckError) else 'MALFORMED_SECOND_SOURCE')
        cells.append(dict(symbol=symbol,**cell))
        progress('HISTORY_CHECK='+symbol+':'+cell['status'],flush=True)
    evidence = dict(provider='YAHOO_PUBLIC_DAILY_CHART', captured_at=now.isoformat(), responses=responses,
        request_end_exclusive=end.isoformat(), execution_authorized=False)
    (root/'manifest.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    return dict(scope='SECOND_VENDOR_DAILY_HISTORY_DIAGNOSTIC_V1', execution_authorized=False, promotion='NONE',
        original_report_sha256=digest(report_path),data=provenance, second_source=evidence,
        tolerance=dict(adverse_difference_bps=str(PRICE_BPS),absolute_price_floor=str(ABS_PRICE)),results=cells,
        status='REVIEW_REQUIRED' if any(c['status']!='DAILY_PRICES_WITHIN_DIAGNOSTIC_TOLERANCE' for c in cells) else 'DAILY_AGREEMENT_ONLY',
        source_sha256=digest(Path(__file__)),
        limitations=['Daily agreement does not verify minute ordering, opening-five volume, quote availability or fills',
            'Distinct vendor is not proof of independent underlying exchange data or authoritative truth',
            'Raw quote OHLC is compared; adjusted-close is deliberately unused; splits require separate reconciliation',
            'Daily auctions, sale-condition rules, corrections and rounding can differ; differences do not prove either feed wrong',
            'Daily volume is descriptive only; no volume acceptance threshold',
            'No historical file is replaced; provider failure is inconclusive; no strategy is promoted'])

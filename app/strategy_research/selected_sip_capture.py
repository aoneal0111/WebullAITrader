"""Capture historical SIP into a separate research directory, never into Atlas inputs."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, time as clocktime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from .episode_price_study import BarCache, NY, SOURCE_SHA
from .episode_gap_audit import bounded_json, coverage

ENDPOINT = 'https://data.alpaca.markets/v2/stocks/bars'
MAX_PAGE_BYTES = 4_000_000


class CaptureError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_page(params, credentials):
    request = urllib.request.Request(ENDPOINT + '?' + urllib.parse.urlencode(params), headers={
        'APCA-API-KEY-ID': credentials[0], 'APCA-API-SECRET-KEY': credentials[1], 'Accept': 'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(MAX_PAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise CaptureError('HTTP_' + str(exc.code)) from None
    except OSError:
        raise CaptureError('NETWORK_ERROR') from None
    if len(raw) > MAX_PAGE_BYTES:
        raise CaptureError('RESPONSE_BYTE_BOUND')
    return raw


def credentials_from_local(repo):
    # Do not rewrite, print, transmit to Codex, or request regeneration of credentials.
    from dotenv import dotenv_values
    values = {**dotenv_values(Path(repo) / '.env'), **os.environ}
    key = values.get('ALPACA_API_KEY') or values.get('APCA_API_KEY_ID') or ''
    secret = values.get('ALPACA_API_SECRET') or values.get('APCA_API_SECRET_KEY') or ''
    return key, secret


def capture(study, output, credentials, *, fetch=request_page, pause=time.sleep, progress=print):
    study, output = Path(study).resolve(), Path(output)
    if len(credentials) != 2 or any(not isinstance(x, str) or not x.strip() for x in credentials):
        raise CaptureError('LOCAL_ALPACA_CREDENTIALS_MISSING')
    summary_path = study / 'episode-price-study-summary.json'
    summary = bounded_json(summary_path, 4_000_000)
    if summary.get('evidence_status') != 'PRICE_REPLAY_WITH_ENTRIES' or summary['corpus']['source_sha256'] != SOURCE_SHA:
        raise CaptureError('CORRECTED_STUDY_REQUIRED')
    with closing(sqlite3.connect((study / 'private-episode-index.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        selected = db.execute('SELECT symbol,day FROM selected ORDER BY day,symbol LIMIT 601').fetchall()
    if not selected or len(selected) > 600 or len(set(selected)) != len(selected) or len(selected) != summary['price_partitions_read']:
        raise CaptureError('SELECTION_COUNT_OR_BOUND')
    for symbol, day in selected:
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,19}', symbol) or not '2026-06-01' <= day <= '2026-08-31':
            raise CaptureError('PARTITION_IDENTITY')
        datetime.strptime(day, '%Y-%m-%d')
    if output.exists():
        raise CaptureError('OUTPUT_EXISTS')
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < 3_000_000_000:
        raise CaptureError('THREE_GB_FREE_SPACE_REQUIRED')
    output.mkdir()
    normalized = output / 'normalized'
    normalized.mkdir()
    raw_root = output / 'raw'
    raw_root.mkdir()
    manifest = dict(scope='SELECTED_HISTORICAL_SIP_CAPTURE_V1', status='PARTIAL', feed='sip', adjustment='raw',
        asof='-', source_summary_sha256=sha256(summary_path.read_bytes()).hexdigest(),
        captured_at=datetime.now(UTC).isoformat(), selected_partitions=len(selected), market_data_attempts=0,
        response_bytes=0, partitions=[], responses=[], orders=0, grok_requests=0, execution_authorized=False,
        original_files_modified=False, error=None,
        limitations=['SIP history is not an executable quote feed or a profitability result.',
            'No missing rows are fabricated; absence can still reflect no eligible trades.',
            'Cross-feed prices and volume differ: regenerate detector inputs before a new strategy test; do not splice SIP into IEX episodes.',
            'No subscription purchase, feed fallback, trading activation, or credential export.'])
    cache = BarCache(normalized)
    try:
        for number, (symbol, day) in enumerate(selected, 1):
            start = datetime.combine(datetime.fromisoformat(day).date(), clocktime(4), NY).astimezone(UTC)
            end = datetime.combine(datetime.fromisoformat(day).date(), clocktime(20), NY).astimezone(UTC)
            if end > datetime.now(UTC) - timedelta(minutes=15):
                raise CaptureError('HISTORICAL_ONLY')
            rows, tokens, token = {}, set(), None
            for page in range(2):
                params = dict(symbols=symbol, timeframe='1Min', start=start.isoformat(),
                    end=(end-timedelta(seconds=1)).isoformat(), feed='sip', adjustment='raw', asof='-', sort='asc', limit=10000)
                if token is not None:
                    params['page_token'] = token
                if manifest['market_data_attempts']:
                    pause(.35)
                manifest['market_data_attempts'] += 1
                raw = fetch(params, credentials)
                if not isinstance(raw, bytes) or len(raw) > MAX_PAGE_BYTES:
                    raise CaptureError('RESPONSE_BYTE_BOUND')
                manifest['response_bytes'] += len(raw)
                if manifest['response_bytes'] > 1_000_000_000:
                    raise CaptureError('TOTAL_RESPONSE_BYTE_BOUND')
                payload = json.loads(raw, parse_float=Decimal)
                batch = payload.get('bars')
                if not isinstance(batch, dict) or set(batch) - {symbol} or not isinstance(batch.get(symbol, []), list):
                    raise CaptureError('RESPONSE_SYMBOL_OR_SCHEMA')
                items = batch.get(symbol, [])
                if len(items) > 10000:
                    raise CaptureError('ROW_BOUND')
                for item in items:
                    at = datetime.fromisoformat(item['t'].replace('Z', '+00:00'))
                    if at.utcoffset() is None or at.second or at.microsecond or not start <= at < end:
                        raise CaptureError('BAR_OUTSIDE_REQUEST_OR_UNALIGNED')
                    values = tuple(str(item[k]) for k in ('o','h','l','c','v'))
                    if at in rows and rows[at] != values:
                        raise CaptureError('CONFLICTING_DUPLICATE')
                    rows[at] = values
                    if len(rows) > 960:
                        raise CaptureError('PARTITION_ROW_BOUND')
                name = f'{symbol}_{day}_{page}.json'
                (raw_root / name).write_bytes(raw)
                manifest['responses'].append(dict(file='raw/' + name, sha256=sha256(raw).hexdigest()))
                token = payload.get('next_page_token')
                if token is None:
                    break
                if not isinstance(token, str) or not token or len(token) > 4096 or token in tokens:
                    raise CaptureError('INVALID_OR_REPEATED_PAGE_TOKEN')
                tokens.add(token)
            else:
                raise CaptureError('PARTITION_PAGE_BOUND')
            path = normalized / f'{symbol}_{day}.jsonl'
            with path.open('x', encoding='utf-8') as stream:
                for at, values in sorted(rows.items()):
                    stream.write(json.dumps(dict(symbol=symbol, timestamp=at.isoformat(),
                        **dict(zip(('open','high','low','close','volume'), values)))) + '\n')
            bars = cache.get(symbol, day)  # Reuse finite OHLCV / chronology validation.
            stats, _ = coverage(bars)
            manifest['partitions'].append(dict(symbol=symbol, day=day, rows=len(bars),
                sha256=cache.proofs[(symbol, day)][2], status='CAPTURED' if bars else 'EMPTY', coverage=dict(stats)))
            (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            progress(f'SIP_PARTITIONS={number}/{len(selected)}; ROWS={len(bars)}', flush=True)
        cache.verify_stable()
        manifest['status'] = 'COMPLETE' if all(p['rows'] for p in manifest['partitions']) else 'COMPLETE_WITH_EMPTY_PARTITIONS'
    except Exception as exc:
        manifest['error'] = str(exc) if isinstance(exc, CaptureError) else 'INVALID_INPUT_OR_PROVIDER_DATA'
    finally:
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        small = {k:v for k,v in manifest.items() if k not in ('responses','partitions')}
        small['captured_partitions'] = len(manifest['partitions'])
        small['observed_bars'] = sum(p['rows'] for p in manifest['partitions'])
        small['empty_partitions'] = sum(not p['rows'] for p in manifest['partitions'])
        small['coverage'] = dict(sum((Counter(p['coverage']) for p in manifest['partitions']), Counter()))
        path = output / 'sip-capture-summary.json'
        path.write_text(json.dumps(small, indent=2), encoding='utf-8')
        progress('CAPTURE_STATUS=' + manifest['status'], flush=True)
        progress('UPLOAD_SUMMARY=' + str(path), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--study', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = capture(args.study, args.output, credentials_from_local(args.repo))
    return 0 if result['status'].startswith('COMPLETE') else 2


if __name__ == '__main__':
    raise SystemExit(main())

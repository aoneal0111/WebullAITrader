"""Extract bounded private exit evidence from a verified closed daily journal."""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
from app.asset_modules.daily_research_journal import canonical, verify_journal, MAX_LINE

MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_RECORDS = 12000


def read_json(path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('METADATA_BOUND')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def extract(session, audit, output, symbols=None):
    session, audit, output = Path(session), Path(audit), Path(output)
    summary = read_json(session/'research-summary.json')
    manifest = read_json(session/'research-manifest.json')
    report = read_json(audit)
    verified = verify_journal(session/'journal')
    if (not verified['evidence_complete'] or verified['baseline_sha256'] != summary['baseline_sha256']
            or verified['baseline_sha256'] != manifest['baseline_sha256']
            or verified['chain_head'] != summary['journal']['chain_head']):
        raise ValueError('SESSION_RECEIPT_OR_BASELINE_MISMATCH')
    start, end = (datetime.fromisoformat(value) for value in
        (manifest['started_at'], summary['journal']['updated_at']))
    selected_symbols = frozenset(symbols or (r['instrument'] for r in report['source']['outcomes']
        if start <= datetime.fromisoformat(r['opened_at']) <= end))
    if not 1 <= len(selected_symbols) <= 10:
        raise ValueError('SYMBOL_SELECTION_BOUND')
    trades = [r for r in report['source']['outcomes']
        if r['instrument'] in selected_symbols and start <= datetime.fromisoformat(r['opened_at'])
        <= datetime.fromisoformat(r['closed_at']) <= end]
    if not trades:
        raise ValueError('NO_MATCHING_CLOSED_TRADES_IN_SESSION')
    windows = [(r['instrument'], datetime.fromisoformat(r['opened_at'])-timedelta(minutes=2),
        datetime.fromisoformat(r['closed_at'])+timedelta(minutes=2)) for r in trades]
    output.mkdir(parents=True, exist_ok=False)
    counts, coalesced = Counter(), Counter()
    last_bucket = {}; total = written = selected = 0; limited = False
    with (output/'private-exit-records.jsonl').open('xb') as dest:
        for path in sorted((session/'journal').glob('events-*.jsonl')):
            with path.open('rb') as source:
                for line in source:
                    if len(line) > MAX_LINE:
                        raise ValueError('JOURNAL_RECORD_BOUND')
                    record = json.loads(line); payload = record['payload']
                    if not isinstance(payload, dict):
                        continue
                    symbol = payload.get('symbol')
                    if symbol not in selected_symbols:
                        continue
                    received = datetime.fromisoformat(record['received_at'])
                    if not any(s == symbol and a <= received <= b for s, a, b in windows):
                        continue
                    kind = record['kind']
                    # Historic ledger replay must not masquerade as new fills.
                    if kind == 'PAPER_EVENT':
                        timestamp = datetime.fromisoformat(payload['timestamp'])
                        if not start <= timestamp <= end:
                            continue
                    elif kind in {'EXIT_DECISION','EXIT_FILL'}:
                        pass
                    elif kind not in {'MARKET_EVENT', 'BARS', 'ASSESSMENT', 'STREAM_ASSESSMENT', 'MARKET_ELIGIBILITY'}:
                        continue
                    else:
                        # At most one non-order record per kind/symbol/second.
                        # These samples are evidence context, never an MFE or
                        # executable-price reconstruction of the full stream.
                        key = kind, symbol; bucket = int(received.timestamp())
                        if last_bucket.get(key) == bucket:
                            coalesced[kind] += 1; continue
                        last_bucket[key] = bucket
                    selected += 1
                    raw = canonical(record)+b'\n'
                    if written >= MAX_OUTPUT_RECORDS or total+len(raw) > MAX_OUTPUT_BYTES:
                        limited = True; continue
                    dest.write(raw); total += len(raw); written += 1; counts[kind] += 1
    # Ensure the closed source receipt did not change while extracting.
    receipt = read_json(session/'journal/journal-status.json')
    if receipt['chain_head'] != verified['chain_head'] or not receipt['closed']:
        raise ValueError('CLOSED_SOURCE_RECEIPT_CHANGED')
    result = dict(scope='VERIFIED_CLOSED_EXIT_TRACE_V1',source=verified,
        session_started_at=start.isoformat(), session_ended_at=end.isoformat(),
        trades=trades,selected_records=selected,written_records=written,
        bytes_written=total,counts=dict(counts),coalesced=dict(coalesced),
        output_capacity_reached=limited,orders=0,network_requests=0,grok_requests=0,
        source_files_modified=False,execution_authorized=False,
        limitations=['Selected symbol trade windows only; other trades are not exported.',
            'Full source chain and closed receipt verified locally before extraction; selected rows are not a standalone chain.',
            'Non-order context sampled at most once per second per kind/symbol; not complete MFE or BBO replay.',
            'No stop-loss guarantee or strategy profitability inferred from this export.'])
    (output/'exit-trace-summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('session','audit','output'):p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--symbols',nargs='+',required=True)
    args=p.parse_args()
    try:result=extract(args.session,args.audit,args.output,args.symbols)
    except (OSError,ValueError,KeyError,TypeError) as exc:p.exit(2,'Exit trace failed: '+type(exc).__name__+'\n')
    print('EXIT_TRACE=PARTIAL_OUTPUT' if result['output_capacity_reached'] else 'EXIT_TRACE=COMPLETE_SELECTED_WINDOWS')
    print('UPLOAD_SUMMARY='+str(args.output/'exit-trace-summary.json'))


if __name__=='__main__':main()

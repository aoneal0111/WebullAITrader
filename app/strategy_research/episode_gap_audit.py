"""Read-only coverage audit of a completed price study; no feed or order access."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from .episode_price_study import BarCache, NY, SOURCE_SHA
from .equity_corpus_review import REPORT


def session(at):
    minute = at.astimezone(NY).hour * 60 + at.astimezone(NY).minute
    return 'PREMARKET' if 240 <= minute < 570 else 'CORE' if 570 <= minute < 960 else 'AFTER_HOURS' if 960 <= minute < 1200 else 'OUTSIDE_EXTENDED'


def coverage(bars):
    counts = Counter()
    examples = []
    for left, right in zip(bars, bars[1:]):
        missing = int((right.timestamp - left.timestamp).total_seconds() // 60) - 1
        if missing <= 0:
            continue
        counts['gap_intervals'] += 1
        counts['missing_minutes_between_observed_bars'] += missing
        counts['gap_length_1' if missing == 1 else 'gap_length_2_to_5' if missing <= 5 else 'gap_length_over_5'] += 1
        at = left.timestamp + timedelta(minutes=1)
        for _ in range(missing):
            counts['missing_' + session(at)] += 1
            at += timedelta(minutes=1)
        if len(examples) < 2:
            examples.append(dict(left_open=left.timestamp.isoformat(), right_open=right.timestamp.isoformat(), missing_minutes=missing))
    return counts, examples


def bounded_json(path, limit):
    if path.stat().st_size > limit:
        raise ValueError('JSON_BYTE_BOUND')
    return json.loads(path.read_text(encoding='utf-8'))


def run(repo, study, output):
    repo, study, output = Path(repo).resolve(), Path(study).resolve(), Path(output)
    summary_path = study / 'episode-price-study-summary.json'
    summary = bounded_json(summary_path, 4_000_000)
    if summary.get('evidence_status') != 'PRICE_REPLAY_WITH_ENTRIES' or summary['corpus']['source_sha256'] != SOURCE_SHA:
        raise ValueError('COMPLETED_CORRECTED_STUDY_REQUIRED')
    expected = bounded_json(study / 'private-price-inputs.json', 1_000_000)
    cache = BarCache((repo / REPORT).parent.parent / 'normalized')
    totals, event_counts = Counter(), Counter()
    examples = []
    db_path = study / 'private-episode-index.sqlite3'
    with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        selected = db.execute('SELECT symbol,day FROM selected ORDER BY day,symbol LIMIT 601').fetchall()
        if not selected or len(selected) > 600 or len(selected) != summary['price_partitions_read']:
            raise ValueError('SELECTED_PARTITION_BOUND_OR_COUNT_MISMATCH')
        for symbol, day in selected:
            if not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,19}', symbol) or not re.fullmatch(r'2026-0[678]-[0-3][0-9]', day):
                raise ValueError('INVALID_PARTITION_IDENTITY')
            bars = cache.get(symbol, day)
            # Match content and original size; touching a file alone is not a data change.
            proof = cache.proofs[(symbol, day)]
            old = expected.get(f'{symbol}_{day}')
            if not old or proof[0] != old[0] or proof[2] != old[2]:
                raise ValueError('PRICE_CONTENT_DIFFERS_FROM_STUDY')
            counts, gaps = coverage(bars)
            totals.update(counts)
            totals['partitions'] += 1
            totals['partitions_with_gaps'] += bool(counts['gap_intervals'])
            totals['observed_bars'] += len(bars)
            if gaps and len(examples) < 12:
                examples.append(dict(symbol=symbol, day=day, gaps=gaps))
            available = {bar.timestamp for bar in bars}
            rows = db.execute('SELECT entry_time FROM events WHERE symbol=? AND day=? LIMIT 50001', (symbol, day)).fetchall()
            if len(rows) > 50000:
                raise ValueError('PARTITION_EVENT_BOUND')
            for (when,) in rows:
                at = datetime.fromisoformat(when)
                if at.utcoffset() is None:
                    raise ValueError('NAIVE_ENTRY_TIME')
                event_counts['selected_episodes'] += 1
                present, previous = at in available, at - timedelta(minutes=1) in available
                event_counts['exact_entry_present' if present else 'exact_entry_missing'] += 1
                if not previous:
                    event_counts['previous_completed_minute_missing'] += 1
                if not present:
                    event_counts['entry_missing_' + session(at)] += 1
                    if bars and bars[0].timestamp < at < bars[-1].timestamp:
                        event_counts['entry_missing_inside_observed_span'] += 1
                    else:
                        event_counts['entry_missing_outside_observed_span'] += 1
    cache.verify_stable()
    report = dict(scope='OFFLINE_PRICE_STUDY_GAP_AUDIT_V1',
        source_summary_sha256=sha256(summary_path.read_bytes()).hexdigest(),
        price_content_matches_study=True, coverage=dict(totals), entry_minutes=dict(event_counts),
        bounded_gap_examples=examples, cause='UNDETERMINED_WITHOUT_PROVIDER_OR_CAPTURE_PROVENANCE',
        orders=0, network_requests=0, grok_requests=0, strategy_modified=False,
        limitations=['Missing normalized rows do not prove a feed outage or a no-trade interval.',
            'Only gaps between observed bars are counted; no assumption of full extended-session coverage.',
            'Entry-minute counts cover selected episodes before portfolio and risk filters, not independent trades.',
            'No bars fabricated, files repaired, strategy enabled, or profitability estimated.'])
    output.mkdir(parents=True, exist_ok=False)
    path = output / 'gap-audit-summary.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('UPLOAD_SUMMARY=' + str(path))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--study', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    run(args.repo, args.study, args.output)


if __name__ == '__main__':
    main()

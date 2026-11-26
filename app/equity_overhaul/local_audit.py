"""Small-output local audit. Raw minute history stays on the operator's PC."""
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from app.strategy_research.candidates import Bar, validate_bars
from .validation import PLAN, SYMBOLS, fingerprint, schedule, _safe_file
from .reference import reference_trades, SPEC

EASTERN = ZoneInfo('America/New_York')
# Retrospective diagnostics, not a newly untouched performance trial.
SCENARIOS = (
    ('frictionless_diagnostic', D(0), D(0)),
    ('low_cost_diagnostic', D(1), D('.0035')),
    ('normal', D(5), D('.0035')),
    ('stress', D(10), D('.007')),
)
NUMERIC = ('entry', 'exit', 'stop', 'net_pnl_per_share', 'net_r')
TEXT = ('policy_id', 'symbol', 'signal_at', 'entry_at', 'exit_at', 'reason')


def digest(path):
    h = sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def read_json(path, limit):
    path = Path(path)
    if path.stat().st_size > limit: raise ValueError('input exceeds size bound')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def source_matches(hashes):
    root = Path(__file__).resolve().parents[1]
    allowed = ('equity_overhaul/validation.py', 'equity_overhaul/history.py',
        'equity_overhaul/replay.py', 'equity_overhaul/strategies.py',
        'strategy_research/candidates.py', 'strategy_research/replay.py', 'market/calendar.py')
    if set(hashes) != set(allowed): return False
    for name in allowed:
        raw = (root/name).read_bytes(); lf = raw.replace(b'\r\n', b'\n')
        if hashes[name] not in {sha256(raw).hexdigest(), sha256(lf).hexdigest(),
                                sha256(lf.replace(b'\n', b'\r\n')).hexdigest()}:
            return False
    return True


def compare(expected, actual):
    mismatches = 0; examples = []
    for i in range(max(len(expected), len(actual))):
        if i >= len(expected) or i >= len(actual):
            bad = ['TRADE_COUNT']
        else:
            a, b = expected[i], actual[i]
            bad = [k for k in TEXT if a[k] != b[k]]
            for k in NUMERIC:
                x, y = D(a[k]), D(b[k])
                if not x.is_finite() or not y.is_finite() or abs(x-y) > D('1e-18'):
                    bad.append(k)
        if bad:
            mismatches += 1
            if len(examples) < 3: examples.append({'trade_index': i, 'fields': bad})
    return {'expected_trades': len(expected), 'reference_trades': len(actual),
            'mismatched_records': mismatches, 'examples': examples}


def summary(trades):
    values = [D(t['net_r']) for t in trades]
    gains = sum((max(v, D(0)) for v in values), D(0))
    losses = -sum((min(v, D(0)) for v in values), D(0))
    n = len(values)
    return {'trades': n, 'wins': sum(v > 0 for v in values),
        'win_rate': str(D(sum(v > 0 for v in values))/n) if n else None,
        'expectancy_r': str(sum(values, D(0))/n) if n else None,
        'profit_factor_r': str(gains/losses) if losses else None,
        'exit_reasons': dict(Counter(t['reason'] for t in trades))}


def run_local_audit(history, report_path, *, progress=print):
    history = Path(history); report_path = Path(report_path)
    report = read_json(report_path, 40_000_000)
    manifest = read_json(history/'manifest.json', 2_000_000)
    plan = report['plan_document']
    if (report.get('scope') != 'OFFLINE_EQUITY_TEMPORAL_SCREEN' or
        plan['plan'] != PLAN or fingerprint(plan['plan']) != plan['plan_sha256'] or
        manifest['plan_document'] != plan or not source_matches(plan['source_hashes'])):
        raise ValueError('original plan/source/report identity mismatch')
    if digest(history/'manifest.json') != report['data']['manifest_sha256']:
        raise ValueError('manifest hash mismatch')
    inputs = manifest['normalized_inputs']
    if len(inputs) != len(SYMBOLS): raise ValueError('all six normalized symbols required')
    wanted = {symbol+'-normalized.json' for symbol in SYMBOLS}
    if {x['file'] for x in inputs} != wanted: raise ValueError('exact normalized basenames required')
    if {x['file']: x['sha256'] for x in inputs} != report['data']['normalized_inputs']:
        raise ValueError('report/input hash inventory mismatch')
    comparisons = []; cohorts = {}; coverage = []; hashes = {}
    expected_cells = {(w['name'], s, policy, cost) for w in PLAN['windows'] for s in SYMBOLS
                      for policy in SPEC for cost in ('normal', 'stress')}
    actual_cells = [(c['window'], c['symbol'], c['policy_id'], c['cost']) for c in report['results']]
    if set(actual_cells) != expected_cells or len(actual_cells) != len(expected_cells):
        raise ValueError('missing or duplicate report cells')
    indexed = {key: c for key, c in zip(actual_cells, report['results'])}
    # One symbol at a time to avoid retaining all million candles in memory.
    for symbol in SYMBOLS:
        path = _safe_file(history, symbol+'-normalized.json')
        hashes[path.name] = digest(path)
        item = next(x for x in inputs if x['file'] == path.name)
        if hashes[path.name] != item['sha256']: raise ValueError('normalized file hash mismatch')
        rows = read_json(path, 80_000_000)
        if not isinstance(rows, list) or not 0 < len(rows) <= 400_000:
            raise ValueError('bounded nonempty normalized rows required')
        bars = tuple(Bar(symbol=r['symbol'],
            **{k: datetime.fromisoformat(r[k]) for k in ('opened','closed','session_end')},
            **{k: D(str(r[k])) for k in ('open','high','low','close','volume')}) for r in rows)
        del rows
        validate_bars(bars, 'minute')
        if bars[0].symbol != symbol: raise ValueError('normalized symbol mismatch')
        days = schedule(bars[0].opened.astimezone(EASTERN).date(),
                        bars[-1].opened.astimezone(EASTERN).date()+timedelta(days=1))
        for b in bars:
            day = b.opened.astimezone(EASTERN).date()
            if day not in days or b.session_end != days[day][1] or b.opened < days[day][0]:
                raise ValueError('bar/calendar mismatch')
        for window in PLAN['windows']:
            start = date.fromisoformat(window['start']); end = date.fromisoformat(window['end_exclusive'])
            sample = tuple(b for b in bars if start-timedelta(days=40) <= b.opened.astimezone(EASTERN).date() < end)
            expected_dates = set(schedule(start, end))
            received = {b.opened.astimezone(EASTERN).date() for b in sample if b.opened.astimezone(EASTERN).date() >= start}
            coverage.append({'window': window['name'], 'symbol': symbol,
                             'expected_sessions': len(expected_dates), 'verified_sessions': len(received & expected_dates)})
            for policy in SPEC:
                applicable = policy != 'NOISE_BAND_LONG_V1' or symbol in ('SPY', 'QQQ')
                for scenario, bps, fee in SCENARIOS:
                    trades = reference_trades(sample, policy, bps, fee, start, end) if applicable else []
                    key = (window['name'], policy, scenario)
                    cohorts.setdefault(key, []).extend(trades)
                    if scenario in ('normal', 'stress'):
                        cell = indexed[(window['name'], symbol, policy, scenario)]
                        if cell['applicable'] != applicable: raise ValueError('report applicability mismatch')
                        result = compare(cell['trades'], trades)
                        comparisons.append({'window': window['name'], 'symbol': symbol,
                                            'policy_id': policy, 'cost': scenario, **result})
            progress('AUDIT_PROGRESS='+symbol+':'+window['name'], flush=True)
    failed = sum(c['mismatched_records'] > 0 for c in comparisons)
    aggregates = [{'window': window, 'policy_id': policy, 'cost': scenario,
                   **summary(ts)} for (window, policy, scenario), ts in cohorts.items()]
    # No trade/candle arrays, paths, credentials or raw responses in the output.
    return {'scope': 'LOCAL_INDEPENDENT_EQUITY_REPLAY_AUDIT_V1',
        'execution_authorized': False, 'promotion': 'NONE',
        'status': 'REPLAY_MISMATCH' if failed else 'ALL_REPORTED_TRADES_MATCH_REFERENCE',
        'report_sha256': digest(report_path), 'manifest_sha256': digest(history/'manifest.json'),
        'normalized_input_hashes': hashes, 'original_plan_sha256': plan['plan_sha256'],
        'audit_source_hashes': {name: digest(Path(__file__).with_name(name))
                                for name in ('local_audit.py', 'reference.py')},
        'comparison_cells': len(comparisons), 'mismatched_cells': failed,
        'comparisons': comparisons, 'coverage': coverage, 'aggregates': aggregates,
        'limitations': ['Reference independently expresses signals and trade traversal; shares Bar schema and exchange calendar',
            'No raw-provider response verification, executable quote/fill evidence or portfolio capacity validation',
            'All cost scenarios are retrospective diagnostics on already reviewed periods, not new holdouts',
            'A reference match does not establish profitability or broker execution parity']}

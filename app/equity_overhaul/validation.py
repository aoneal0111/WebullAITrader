"""Offline temporal screening. Nothing here can grant execution authority.

The plan is committed before a dataset is selected; historical splits are always
labelled retrospective, never silently represented as prospective evidence.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import random
import re
from zoneinfo import ZoneInfo

from app.market.calendar import NYSE
from app.strategy_research.candidates import Bar, validate_bars
from app.strategy_research.replay import Costs
from .replay import replay
from .strategies import RULES, rule_fingerprint

EASTERN = ZoneInfo('America/New_York')
SYMBOLS = ('SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSLA')
# Do not optimize these thresholds after viewing the holdout. Failure is retained.
PLAN = {
    'version': 'EQUITY_TEMPORAL_SCREEN_V1',
    'evidence_kind': 'RETROSPECTIVE_SEPARATE_PERIODS',
    'symbols': list(SYMBOLS),
    'windows': [
        {'name': 'development', 'start': '2025-01-02', 'end_exclusive': '2026-01-01'},
        {'name': 'validation', 'start': '2026-01-01', 'end_exclusive': '2026-07-01'},
        {'name': 'holdout', 'start': '2026-07-01', 'end_exclusive': '2026-09-25'},
    ],
    'warmup_calendar_days': 40,
    'costs_per_side': [
        {'name': 'normal', 'adverse_bps': '5', 'fee_per_share': '0.0035'},
        {'name': 'stress', 'adverse_bps': '10', 'fee_per_share': '0.007'},
    ],
    'screen_min_trades_per_policy_window': 100,
    'screen_min_complete_dates_per_symbol_window': 30,
    'screen_min_win_rate': '0.50',
    'screen_min_session_coverage': '0.98',
    'cluster_bootstrap_replicates': 2000,
    'cluster_bootstrap_seed': 734921,
    'execution_authorized': False,
}


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_hashes():
    root = Path(__file__).resolve().parents[1]
    names = ('equity_overhaul/validation.py', 'equity_overhaul/history.py', 'equity_overhaul/replay.py',
             'equity_overhaul/strategies.py', 'strategy_research/candidates.py',
             'strategy_research/replay.py', 'market/calendar.py')
    return {name: sha256((root / name).read_bytes()).hexdigest() for name in names}


def frozen_plan():
    return {'plan': json.loads(json.dumps(PLAN)), 'plan_sha256': fingerprint(PLAN), 'source_hashes': source_hashes()}


def schedule(start, end):
    rows = NYSE.schedule(start_date=start, end_date=end - timedelta(days=1))
    return {r.market_open.to_pydatetime().astimezone(EASTERN).date():
            (r.market_open.to_pydatetime(), r.market_close.to_pydatetime())
            for _, r in rows.iterrows()}


def _safe_file(root, name):
    if not isinstance(name, str) or Path(name).name != name or not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise ValueError('manifest must reference a local basename')
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise ValueError('manifest path escapes input directory')
    return path


def load_dataset(root):
    """Read the existing public-capture normalized format, checking actual bytes.

    Hashes establish reproducibility, not authenticity of a self-supplied manifest.
    Calendar coverage is independently reconstructed rather than trusted from it.
    """
    root = Path(root)
    raw_manifest = (root / 'manifest.json').read_bytes()
    if len(raw_manifest) > 2_000_000:
        raise ValueError('manifest exceeds bound')
    manifest = json.loads(raw_manifest)
    items = manifest.get('normalized_inputs', [])
    if not isinstance(items, list) or len(items) > len(SYMBOLS):
        raise ValueError('at most six normalized inputs required')
    datasets = {}; hashes = {}
    for item in items:
        name = item['file']; path = _safe_file(root, name)
        if path.stat().st_size > 80_000_000:
            raise ValueError('normalized file exceeds bound')
        raw = path.read_bytes(); digest = sha256(raw).hexdigest()
        if digest != item['sha256']:
            raise ValueError('normalized input hash mismatch')
        rows = json.loads(raw, parse_float=D)
        if not isinstance(rows, list) or not 0 < len(rows) <= 400_000:
            raise ValueError('nonempty bounded bar list required')
        bars = tuple(Bar(symbol=r['symbol'],
                         **{k: datetime.fromisoformat(r[k]) for k in ('opened', 'closed', 'session_end')},
                         **{k: D(str(r[k])) for k in ('open', 'high', 'low', 'close', 'volume')})
                     for r in rows)
        validate_bars(bars, 'minute')
        symbol = bars[0].symbol
        if symbol not in SYMBOLS or symbol in datasets:
            raise ValueError('unexpected or duplicate symbol')
        # Validate real exchange hours: a fabricated short session must fail.
        days = schedule(bars[0].opened.astimezone(EASTERN).date(),
                        bars[-1].opened.astimezone(EASTERN).date() + timedelta(days=1))
        for b in bars:
            day = b.opened.astimezone(EASTERN).date()
            if day not in days or b.session_end != days[day][1] or b.opened < days[day][0]:
                raise ValueError('bar disagrees with exchange calendar')
        datasets[symbol] = bars; hashes[name] = digest
    return datasets, {'manifest_sha256': sha256(raw_manifest).hexdigest(),
                      'normalized_inputs': hashes,
                      'source': manifest.get('source', 'UNSPECIFIED')}


def metrics(trades, complete_dates, *, bootstrap=True):
    """R statistics, not account dollars. Resample whole dates across symbols.

    Same-day trades are dependent. A seeded date-cluster percentile interval is
    descriptive evidence, not an independence claim or a profitability guarantee.
    Include zero-trade dates in the resampling universe.
    """
    dates = sorted(set(complete_dates))
    daily = {day: [D(0), 0] for day in dates}
    values = []
    for t in trades:
        value = D(t['net_r'])
        if not value.is_finite():
            raise ValueError('nonfinite trade result')
        day = datetime.fromisoformat(t['entry_at']).astimezone(EASTERN).date().isoformat()
        if day not in daily:
            raise ValueError('trade outside evaluated sessions')
        daily[day][0] += value; daily[day][1] += 1; values.append(value)
    n = len(values); wins = sum(v > 0 for v in values)
    gains = sum((max(v, D(0)) for v in values), D(0))
    losses = -sum((min(v, D(0)) for v in values), D(0))
    ci = None
    if bootstrap and n and len(dates) >= 2:
        rng = random.Random(PLAN['cluster_bootstrap_seed']); draws = []
        clusters = list(daily.values())
        for _ in range(PLAN['cluster_bootstrap_replicates']):
            chosen = rng.choices(clusters, k=len(clusters))
            count = sum(x[1] for x in chosen)
            if count: draws.append(sum((x[0] for x in chosen), D(0)) / count)
        if draws:
            draws.sort(); ci = [str(draws[int((len(draws)-1)*p)]) for p in (.025, .975)]
    return {'closed_trades': n, 'wins': wins, 'losses': sum(v < 0 for v in values),
            'breakeven': sum(v == 0 for v in values),
            'win_rate': str(D(wins)/n) if n else None,
            'expectancy_r': str(sum(values, D(0))/n) if n else None,
            'profit_factor_r': str(gains/losses) if losses else None,
            'gross_positive_r': str(gains), 'gross_negative_r': str(losses),
            'date_cluster_expectancy_95pct_interval_r': ci,
            'complete_dates': len(dates),
            'note': 'Per-trade R statistics; not shared-capital returns or portfolio drawdown'}


def evaluate(root, plan_document):
    """Evaluate only the frozen plan. No subset winner or registry mutation."""
    if plan_document != frozen_plan():
        raise ValueError('plan or source changed; freeze a new explicitly reviewed experiment')
    datasets, provenance = load_dataset(root)
    results = []; aggregates = []; coverage = []
    for window in PLAN['windows']:
        start, end = (date.fromisoformat(window[k]) for k in ('start', 'end_exclusive'))
        expected = set(schedule(start, end))
        prepared = {}; complete = {}
        for symbol in SYMBOLS:
            bars = datasets.get(symbol, ())
            chosen = tuple(b for b in bars if start - timedelta(days=40) <= b.opened.astimezone(EASTERN).date() < end)
            available = {b.opened.astimezone(EASTERN).date() for b in chosen}
            covered = expected & available
            complete[symbol] = {d.isoformat() for d in covered}
            coverage.append({'window': window['name'], 'symbol': symbol,
                             'expected_sessions': len(expected), 'complete_sessions': len(covered),
                             'coverage': str(D(len(covered))/len(expected)) if expected else '0',
                             'missing_dates': [d.isoformat() for d in sorted(expected - available)]})
            prepared[symbol] = chosen
        for policy in RULES:
            symbols = ('SPY', 'QQQ') if policy == 'NOISE_BAND_LONG_V1' else SYMBOLS
            for cost in PLAN['costs_per_side']:
                all_trades = []; all_dates = set(); blockers = []
                for symbol in SYMBOLS:
                    applicable = symbol in symbols
                    trades = []
                    if applicable and prepared[symbol] and complete[symbol]:
                        r = replay(prepared[symbol], policy,
                                   Costs(D(cost['adverse_bps']), D(cost['fee_per_share'])),
                                   evaluate_from=start, evaluate_until=end)
                        if r['unresolved_positions']: raise ValueError('unresolved replay position')
                        trades = r['trades']
                    results.append({'window': window['name'], 'symbol': symbol, 'policy_id': policy,
                                    'cost': cost['name'], 'applicable': applicable,
                                    'metrics': metrics(trades, complete[symbol], bootstrap=False) if applicable else None,
                                    'trades': trades})
                    if applicable:
                        all_trades.extend(trades); all_dates.update(complete[symbol])
                        fraction = D(len(complete[symbol]))/len(expected) if expected else D(0)
                        if fraction < D(PLAN['screen_min_session_coverage']):
                            blockers.append(f'{symbol}:INSUFFICIENT_SESSION_COVERAGE')
                        if len(complete[symbol]) < PLAN['screen_min_complete_dates_per_symbol_window']:
                            blockers.append(f'{symbol}:INSUFFICIENT_DATES')
                        # Missing warmup sessions also block a noise-band cell.
                        if policy == 'NOISE_BAND_LONG_V1':
                            prior = sorted(d for d in schedule(start-timedelta(days=40), start))[-14:]
                            available = {b.opened.astimezone(EASTERN).date() for b in prepared[symbol]}
                            if len(prior) < 14 or not set(prior) <= available:
                                blockers.append(f'{symbol}:INCOMPLETE_WARMUP')
                summary = metrics(all_trades, all_dates)
                if summary['closed_trades'] < PLAN['screen_min_trades_per_policy_window']:
                    blockers.append('INSUFFICIENT_TRADES')
                if summary['expectancy_r'] is None or D(summary['expectancy_r']) <= 0:
                    blockers.append('NONPOSITIVE_OR_MISSING_NET_EXPECTANCY')
                if summary['win_rate'] is None or D(summary['win_rate']) < D(PLAN['screen_min_win_rate']):
                    blockers.append('WIN_RATE_BELOW_RESEARCH_TARGET')
                ci = summary['date_cluster_expectancy_95pct_interval_r']
                if ci is None or D(ci[0]) <= 0:
                    blockers.append('EXPECTANCY_INTERVAL_INCLUDES_NONPOSITIVE')
                aggregates.append({'window': window['name'], 'policy_id': policy, 'cost': cost['name'],
                                   'metrics': summary, 'research_screen_pass': not blockers,
                                   'blockers': blockers})
    return {'scope': 'OFFLINE_EQUITY_TEMPORAL_SCREEN', 'execution_authorized': False,
            'promotion': 'NONE', 'plan_document': plan_document, 'data': provenance,
            'coverage': coverage, 'results': results, 'aggregates': aggregates,
            'deployment_blockers': ['NO_QUOTE_VERIFIED_EXECUTION_COSTS', 'NO_SHARED_CAPITAL_PORTFOLIO_REPLAY',
                                    'NO_MATCHED_FORWARD_PAPER_EVIDENCE', 'NO_PRODUCTION_SIGNAL_ADAPTER_PARITY',
                                    'NO_POINT_IN_TIME_FULL_MARKET_UNIVERSE', 'NO_PROSPECTIVE_HOLDOUT'],
            'limitations': ['Fixed liquid-symbol universe; cannot validate full-market momentum selection',
                            'Historical partitioning alone does not establish unseen or prospective evidence',
                            'Coverage exclusions and zero-trade cells are retained; no best-symbol promotion',
                            'Costs are fixed assumptions, not verified Webull spreads, slippage or liquidity']}

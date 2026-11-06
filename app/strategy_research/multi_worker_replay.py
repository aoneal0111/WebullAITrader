"""Offline spot research: shared candles, strategy workers, one unlevered wallet.

Reuses frozen Atlas RSI2/Donchian hypotheses, not NFI or the Instagram bot.
No broker, model client, credentials, live subscription or automatic promotion.
"""
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path

from .crypto_screening import Bar, signals, validate, epoch

FAMILIES = ('RSI2_TREND', 'DONCHIAN20_TREND')
DAY = 86400


@dataclass(frozen=True)
class Worker:
    id: str
    symbol: str
    family: str


def workers_for(symbols):
    return tuple(Worker(f'{family}:{symbol}', symbol, family)
                 for family in FAMILIES for symbol in sorted(symbols))


def replay(data, workers, *, start, end, fee=.001, slippage=.001,
           initial_cash=10000., risk_fraction=.005, allocation_fraction=.10,
           decisions=None):
    """Completed-close decisions; next-open fills; stops may gap below planned risk.

    All workers compete for actual shared cash. One lifecycle per symbol; fixed
    worker-ID priority, not a hindsight winner. Intrabar exits cannot fund an
    earlier open. Exit ownership stays with the entry worker. Terminal positions
    reserve liquidation costs and never count as closed wins.
    """
    settings = (fee, slippage, initial_cash, risk_fraction, allocation_fraction)
    if (not all(math.isfinite(x) for x in settings) or not 0 <= fee < .1
            or not 0 <= slippage < .1 or initial_cash <= 0
            or not 0 < risk_fraction <= 1 or not 0 < allocation_fraction <= 1
            or type(start) is not int or type(end) is not int or start >= end):
        raise ValueError('INVALID_REPLAY_SETTINGS')
    if not data or len(data) > 100 or not workers or len(workers) > 1000:
        raise ValueError('BOUNDED_DATA_AND_WORKERS_REQUIRED')
    if len({w.id for w in workers}) != len(workers):
        raise ValueError('UNIQUE_WORKER_IDS_REQUIRED')
    calendars = []
    for bars in data.values():
        validate(bars, DAY)
        calendars.append(tuple(b.start for b in bars))
    if any(c != calendars[0] for c in calendars):
        raise ValueError('IDENTICAL_COMPLETE_CALENDARS_REQUIRED')
    if start < calendars[0][0] + 200*DAY or end > calendars[0][-1] + DAY:
        raise ValueError('FULL_WINDOW_AND_WARMUP_REQUIRED')
    ordered = sorted(workers, key=lambda w: w.id)
    if any(w.symbol not in data or w.family not in FAMILIES for w in ordered):
        raise ValueError('REGISTERED_WORKER_REQUIRED')
    cache = {}
    for w in ordered:
        key = (w.symbol, w.family)
        if key not in cache:
            cache[key] = (signals(data[w.symbol], w.family) if decisions is None else decisions[key])
        if len(cache[key]) != len(calendars[0]):
            raise ValueError('ALIGNED_DECISIONS_REQUIRED')
    cash = peak = initial_cash
    positions, trades, curve = {}, [], []
    rejections, per_worker = Counter(), {w.id: [] for w in ordered}
    max_dd = max_gross = 0.
    minimum_cash = cash
    last_index = None
    max_positions = 0

    def close(symbol, i, raw, reason):
        nonlocal cash
        p = positions.pop(symbol)
        fill = raw*(1-slippage)
        credit = p['quantity']*fill*(1-fee)
        cash += credit
        t = dict(worker=p['worker'], symbol=symbol, family=p['family'],
                 signal_at=p['signal_at'], entry_at=p['entry_at'], exit_at=calendars[0][i],
                 entry=p['entry'], exit=fill, quantity=p['quantity'],
                 debit=p['debit'], credit=credit, pnl=credit-p['debit'], reason=reason)
        trades.append(t)
        per_worker[t['worker']].append(t)

    for i, at in enumerate(calendars[0]):
        if at < start or at + DAY > end:
            continue
        last_index = i
        exited = set()
        # Only exits known at this open can release cash for this open's entries.
        for symbol, p in list(positions.items()):
            b = data[symbol][i]
            previous = cache[symbol, p['family']][i-1]
            hold = 10 if p['family'] == 'RSI2_TREND' else 30
            if b.open <= p['stop']:
                close(symbol, i, b.open, 'GAP_STOP'); exited.add(symbol)
            elif previous[1] or i-p['index'] >= hold:
                close(symbol, i, b.open, 'SIGNAL' if previous[1] else 'TIMEOUT'); exited.add(symbol)
        opening_equity = cash + sum(p['quantity']*data[s][i].open*(1-slippage)*(1-fee)
                                    for s, p in positions.items())
        for w in ordered:
            buy, _, stop = cache[w.symbol, w.family][i-1]
            if not buy:
                continue
            if w.symbol in positions or w.symbol in exited:
                rejections['SYMBOL_ALREADY_OWNED_OR_EXITED'] += 1
                continue
            b = data[w.symbol][i]
            entry = b.open*(1+slippage)
            if stop is None or not math.isfinite(stop) or stop <= 0 or b.open <= stop:
                rejections['INVALID_OR_GAPPED_STOP'] += 1
                continue
            loss = entry*(1+fee)-stop*(1-slippage)*(1-fee)
            quantity = min(opening_equity*risk_fraction/loss,
                           opening_equity*allocation_fraction/(entry*(1+fee)),
                           cash/(entry*(1+fee)))
            if quantity*entry < 1:
                rejections['INSUFFICIENT_SHARED_CASH'] += 1
                continue
            debit = quantity*entry*(1+fee)
            cash -= debit
            if abs(cash) < 1e-8:
                cash = 0.
            minimum_cash = min(minimum_cash, cash)
            positions[w.symbol] = dict(worker=w.id, family=w.family, quantity=quantity,
                entry=entry, stop=stop, debit=debit, signal_at=at-DAY,
                entry_at=at, index=i)
        # Every entry at this open precedes all intrabar stop proceeds.
        max_positions = max(max_positions, len(positions))
        gross = sum(p['quantity']*data[s][i].open for s,p in positions.items())
        # Open-price gross/NAV excludes hypothetical liquidation costs; risk sizing
        # above uses the smaller liquidation equity including exit costs.
        open_nav = cash + gross
        max_gross = max(max_gross, gross/open_nav if open_nav > 0 else 0)
        for symbol, p in list(positions.items()):
            if data[symbol][i].low <= p['stop']:
                close(symbol, i, p['stop'], 'ENTRY_BAR_STOP' if p['index']==i else 'STOP')
        equity = cash + sum(p['quantity']*data[s][i].close*(1-slippage)*(1-fee)
                            for s,p in positions.items())
        peak = max(peak, equity)
        max_dd = max(max_dd, 1-equity/peak)
        curve.append(dict(at=at, cash=cash, liquidation_equity=equity))
    if last_index is None:
        raise ValueError('NO_COMPLETE_EVALUATION_BARS')
    marked = curve[-1]['liquidation_equity']
    realized = sum(t['pnl'] for t in trades)
    unrealized = sum(p['quantity']*data[s][last_index].close*(1-slippage)*(1-fee)-p['debit']
                     for s,p in positions.items())
    residual = marked-initial_cash-realized-unrealized
    if minimum_cash < -1e-7 or abs(residual) > 1e-6:
        raise AssertionError('SHARED_WALLET_RECONCILIATION_FAILED')

    def metrics(items):
        gains = sum(max(t['pnl'], 0) for t in items)
        losses = -sum(min(t['pnl'], 0) for t in items)
        wins = sum(t['pnl'] > 0 for t in items)
        return dict(closed_trades=len(items), wins=wins,
                    win_rate=wins/len(items) if items else None,
                    realized_net=sum(t['pnl'] for t in items),
                    profit_factor=gains/losses if losses else None)
    return dict(**metrics(trades), workers=len(workers), independent_families=len(FAMILIES),
        symbols=len(data), initial_cash=initial_cash, ending_cash=cash,
        ending_liquidation_equity=marked, marked_net=marked-initial_cash,
        unrealized_net=unrealized, open_positions=len(positions),
        maximum_close_drawdown=max_dd, maximum_open_gross_fraction=max_gross,
        minimum_cash=minimum_cash, accounting_residual=residual,
        maximum_simultaneous_positions=max_positions,
        rejections=dict(rejections), costs=dict(fee_per_side=fee, slippage_per_side=slippage),
        per_worker={k: metrics(v) for k,v in per_worker.items()},
        per_family={f: metrics([t for t in trades if t['family']==f]) for f in FAMILIES},
        trades=trades, equity_curve=curve, network_requests=0, grok_requests=0,
        orders=0, execution_authorized=False)


def load_pinned_daily(folder, lock):
    """First 50 alphabetical locked daily files; no result-based universe selection."""
    import pandas as pd
    files = sorted((x for x in lock['files'] if x['path'].endswith('-1d.feather')),
                   key=lambda x: x['path'])[:50]
    start, end = epoch('2022-01-01'), epoch('2025-01-01')
    data, evidence, excluded = {}, [], []
    for item in files:
        path = Path(folder)/Path(item['path']).name
        raw = path.read_bytes()
        import hashlib
        if (len(raw) != item['bytes'] or hashlib.sha1(
                b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != item['git_blob_sha']):
            raise ValueError('PINNED_CANDLE_HASH_MISMATCH')
        frame = pd.read_feather(path)
        bars = tuple(Bar(int(pd.Timestamp(r.date).timestamp()), float(r.open),float(r.high),
                        float(r.low),float(r.close),float(r.volume))
                     for r in frame.itertuples() if start <= int(pd.Timestamp(r.date).timestamp()) < end)
        symbol = Path(item['path']).name.removesuffix('-1d.feather')
        try:
            validate(bars, DAY)
            if bars[0].start != start or bars[-1].start + DAY != end:
                raise ValueError('WINDOW_COVERAGE')
        except ValueError as e:
            excluded.append(dict(symbol=symbol, reason=str(e)))
            continue
        data[symbol] = bars
        evidence.append(dict(path=item['path'],sha256=sha256(raw).hexdigest(), rows=len(bars)))
    if not data:
        raise ValueError('NO_COMPLETE_SOURCE_HISTORY')
    return data, evidence, excluded


def study(folder, lock):
    import time
    data, evidence, excluded = load_pinned_daily(folder, lock)
    workers = workers_for(data)
    began = time.perf_counter()
    decisions = {(s,f): signals(b,f) for s,b in data.items() for f in FAMILIES}
    cases = []
    for label, start, end in (('exploratory_2023','2023-01-01','2024-01-01'),
                              ('later_2024','2024-01-01','2025-01-01')):
        for multiplier in (1,2):
            result = replay(data, workers, start=epoch(start), end=epoch(end),
                            fee=.001*multiplier,slippage=.001*multiplier,decisions=decisions)
            cases.append(dict(window=label,cost_multiplier=multiplier,**result))
    return dict(scope='OFFLINE_SHARED_WALLET_MULTI_WORKER_V1', source_commit=lock['commit'],
        worker_count=len(workers), source_files=evidence,excluded_symbols=excluded,
        duration_seconds=time.perf_counter()-began,cases=cases,promotion='NONE',
        tomorrow_execution_ready=False, blockers=[
            'No live/shadow adapter or execution authority is connected to these workers',
            'Daily candles cannot verify spread, seller/buyer flow, liquidity depth or intraday scalping',
            'Historical market universe contains selection/survivorship bias',
            'No Grok decisions reproduced: candle replay has no recorded model decisions',
            'Existing Warrior/Scalper stale quote issue is outside this research runner'],
        limitations=[
            'Two frozen strategy hypotheses across symbols, not 100 independent proven strategies',
            'All cases retained; later window not certified unseen; no parameter selection',
            'Fractional spot quantity; no venue rounding, minimum notional or order-book fills modeled',
            'Assumed costs; Grok/hosting costs excluded; close-only drawdown misses intraday extremes',
            'Stop losses may gap beyond planned risk; no copied Instagram profits or NFI reproduction'])


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    lock=json.loads((Path(__file__).resolve().parents[1]/'strategy_lab'/'nfi-history-source-lock.json').read_text())
    report=study(a.history,lock)
    with a.output.open('x') as stream:
        json.dump(report,stream,indent=2,allow_nan=False)
    for case in report['cases']:
        print(json.dumps({k:case[k] for k in ('window','cost_multiplier','workers','closed_trades',
             'win_rate','marked_net','maximum_close_drawdown','accounting_residual')}))


if __name__=='__main__':
    main()

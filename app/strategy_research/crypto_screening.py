"""Fixed, causal spot-crypto hypotheses; offline research, no execution imports.

Parameters frozen before the October 23 2026 public-data screen. These are Atlas
adaptations, not reproductions of author-reported bot results. Float arithmetic
is used only for offline comparisons, never for order sizing or broker orders.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
import math
from statistics import mean, pstdev, stdev

from app.indicators.rsi import rsi

CANDIDATES = ('RSI2_TREND', 'DONCHIAN20_TREND', 'BB_RSI14_REVERSION', 'BB_BREAKOUT_CLOUD')
TIMEFRAMES = (21600, 86400)
EXECUTION_AUTHORIZED = False


@dataclass(frozen=True)
class Bar:
    start: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def validate(bars, seconds):
    if seconds not in TIMEFRAMES or not bars:
        raise ValueError('nonempty 6h or daily bars required')
    for i, b in enumerate(bars):
        if (b.start % seconds or any(not math.isfinite(x) for x in
                (b.open, b.high, b.low, b.close, b.volume)) or b.low <= 0 or
                b.volume < 0 or not b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high):
            raise ValueError('invalid OHLCV or alignment')
        if i and b.start != bars[i-1].start + seconds:
            raise ValueError('missing, duplicate or unordered candle; no gap filling')


def daily(bars):
    validate(bars, 21600)
    result = []
    for i in range(0, len(bars), 4):
        group = bars[i:i+4]
        if len(group) != 4 or group[0].start % 86400:
            raise ValueError('complete UTC days required')
        result.append(Bar(group[0].start, group[0].open, max(b.high for b in group),
                          min(b.low for b in group), group[-1].close, sum(b.volume for b in group)))
    return tuple(result)


def signals(bars, candidate):
    if candidate not in CANDIDATES:
        raise ValueError('unknown frozen candidate')
    closes = [b.close for b in bars]
    r2, r14 = rsi(closes, 2), rsi(closes, 14)
    result = []
    for i, b in enumerate(bars):
        enter = leave = False
        stop = None
        if i >= 199:
            window = bars[i-19:i+1]
            stop = min(x.low for x in window) * .9995
            trend = b.close > mean(closes[i-199:i+1])
            if candidate == 'RSI2_TREND':
                enter = trend and r2[i] <= 10
                leave = b.close > mean(closes[i-4:i+1])
            elif candidate == 'DONCHIAN20_TREND':
                enter = trend and b.close > max(x.high for x in bars[i-20:i])
                leave = b.close < min(x.low for x in bars[i-20:i])
            elif candidate == 'BB_RSI14_REVERSION':
                typical = [(x.high+x.low+x.close)/3 for x in window]
                enter = r14[i] < 30 and b.close < mean(typical)-2*stdev(typical)
                leave = r14[i] > 70
            else:
                hl2 = [(x.high+x.low)/2 for x in window]
                mid, upper = mean(hl2), mean(hl2)+2*pstdev(hl2)
                # Cloud visible at t was computed at t-26; never shift future data back.
                projected = bars[:i-26+1]
                def midpoint(n):
                    return (max(x.high for x in projected[-n:])+min(x.low for x in projected[-n:]))/2
                cloud = max((midpoint(9)+midpoint(26))/2, midpoint(52))
                enter = b.close > upper and b.close > cloud
                leave = b.close < mid
        result.append((bool(enter), bool(leave), stop))
    return tuple(result)


def simulate(bars, decisions, *, seconds, start, end, candidate, fee=.001, slippage=.001):
    """One $10k wallet per pair; 0.5% initial stop risk, 20% maximum allocation.

    Signals at completed close fill at the next open. Gap stops use the worse
    open; intrabar stop precedes target. Final open positions are marked, never
    counted as winning trades. Windows start flat and never trade before start.
    """
    validate(bars, seconds)
    if len(decisions) != len(bars) or candidate not in CANDIDATES:
        raise ValueError('aligned decisions and frozen candidate required')
    if not (0 <= fee < .1 and 0 <= slippage < .1 and start < end):
        raise ValueError('invalid costs or dates')
    cash = peak = 10000.
    position = None
    trades = []
    drawdown = 0.
    last = None
    max_hold = 10 if candidate == 'RSI2_TREND' else 30
    for i, b in enumerate(bars):
        if b.start + seconds > end:
            break  # Partial/future bar cannot change a decision, fill or mark.
        if b.start < start:
            continue
        last = b
        previous = decisions[i-1] if i else (False, False, None)
        if position:
            entry_i, qty, entry, stop, debit, entry_at = position
            target = entry*1.1 if candidate == 'BB_RSI14_REVERSION' else None
            raw = reason = None
            if b.open <= stop:
                raw, reason = b.open, 'GAP_STOP'
            elif previous[1] or i-entry_i >= max_hold:
                raw, reason = b.open, 'SIGNAL' if previous[1] else 'TIMEOUT'
            elif b.low <= stop:
                raw, reason = stop, 'STOP'
            elif target is not None and b.high >= target:
                raw, reason = max(b.open, target), 'TARGET'
            if raw is not None:
                fill = raw*(1-slippage)
                credit = qty*fill*(1-fee)
                cash += credit
                trades.append(dict(entry_at=entry_at, exit_at=b.start, entry=entry, exit=fill,
                                   quantity=qty, pnl=credit-debit, return_pct=(credit/debit-1)*100,
                                   reason=reason))
                position = None
                # Avoid same-bar exit/re-entry and optimistic intrabar sequence assumptions.
                equity = cash
                peak = max(peak, equity)
                drawdown = max(drawdown, 1-equity/peak)
                continue
        elif previous[0] and previous[2] is not None:
            entry, stop = b.open*(1+slippage), previous[2]
            # A gap through the predeclared stop invalidates the setup.
            if b.open > stop and entry > stop:
                loss_per_unit = entry*(1+fee)-stop*(1-slippage)*(1-fee)
                qty = min(cash*.005/loss_per_unit, cash*.20/(entry*(1+fee)))
                debit = qty*entry*(1+fee)
                cash -= debit
                position = (i, qty, entry, stop, debit, b.start)
                if b.low <= stop:
                    fill = stop*(1-slippage)
                    credit = qty*fill*(1-fee)
                    cash += credit
                    trades.append(dict(entry_at=b.start, exit_at=b.start, entry=entry, exit=fill,
                                       quantity=qty, pnl=credit-debit, return_pct=(credit/debit-1)*100,
                                       reason='ENTRY_BAR_STOP'))
                    position = None
                elif candidate == 'BB_RSI14_REVERSION' and b.high >= entry*1.1:
                    fill = entry*1.1*(1-slippage)
                    credit = qty*fill*(1-fee)
                    cash += credit
                    trades.append(dict(entry_at=b.start, exit_at=b.start, entry=entry, exit=fill,
                                       quantity=qty, pnl=credit-debit, return_pct=(credit/debit-1)*100,
                                       reason='ENTRY_BAR_TARGET'))
                    position = None
        equity = cash + (position[1]*b.close*(1-slippage)*(1-fee) if position else 0)
        peak = max(peak, equity)
        drawdown = max(drawdown, 1-equity/peak)
    equity = cash + (position[1]*last.close*(1-slippage)*(1-fee) if position and last else 0)
    wins = sum(t['pnl'] > 0 for t in trades)
    gains = sum(max(t['pnl'], 0) for t in trades)
    losses = -sum(min(t['pnl'], 0) for t in trades)
    return dict(candidate=candidate, seconds=seconds, closed_trades=len(trades), wins=wins,
                losses=sum(t['pnl'] < 0 for t in trades),
                win_rate=wins/len(trades) if trades else None,
                profit_factor=gains/losses if losses else None,
                net_return_pct=(equity/10000-1)*100, max_drawdown_pct=drawdown*100,
                open_positions=int(position is not None), ending_equity=equity, trades=trades)


def replay(bars, candidate, **kwargs):
    return simulate(bars, signals(bars, candidate), candidate=candidate, **kwargs)


def epoch(iso):
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp())


def screen(data):
    windows = (('development_2022_2023', '2022-01-01', '2024-01-01'),
               ('validation_2024', '2024-01-01', '2025-01-01'),
               ('validation_2025', '2025-01-01', '2026-01-01'),
               ('validation_2026', '2026-01-01', '2026-10-23'))
    rows = []
    for symbol, source in sorted(data.items()):
        for seconds in TIMEFRAMES:
            bars = source if seconds == 21600 else daily(source)
            for candidate in CANDIDATES:
                decisions = signals(bars, candidate)
                for label, start, end in windows:
                    for stress in (1, 2):
                        row = simulate(bars, decisions, seconds=seconds, start=epoch(start), end=epoch(end),
                                       candidate=candidate, fee=.001*stress, slippage=.001*stress)
                        rows.append(dict(symbol=symbol, window=label, cost_multiplier=stress, **row))
    # Frozen gate across BOTH pairs and ALL three later windows, normal and stress.
    # Passing is research eligibility only, never execution authorization.
    gates = []
    for seconds in TIMEFRAMES:
        for candidate in CANDIDATES:
            later = [r for r in rows if r['seconds']==seconds and r['candidate']==candidate
                     and r['window'].startswith('validation')]
            normal = [r for r in later if r['cost_multiplier']==1]
            count = sum(r['closed_trades'] for r in normal)
            wins = sum(r['wins'] for r in normal)
            eligible = (len(later)==12 and count>=100 and wins/count>=.55
                        and all(r['closed_trades']>=10 and r['net_return_pct']>0
                                and r['profit_factor'] is not None and r['profit_factor']>=1.2
                                and r['max_drawdown_pct']<=15 for r in later))
            gates.append(dict(candidate=candidate, seconds=seconds, later_trades=count,
                              later_win_rate=wins/count if count else None, research_eligible=eligible))
    return dict(execution_authorized=False, scope='EXPLORATORY_EXTERNAL_VENUE_RESEARCH',
                candidates=list(CANDIDATES), rows=rows, gates=gates)

"""Conservative OHLC screening, not broker simulation or production approval.

Use tick/quote replay before promotion. One unit per trade; returns in initial
risk units allow comparison without implying portfolio profits or capacity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from math import sqrt

from .candidates import Bar, Candidate, DAILY, entry_signal, exit_signal, validate_bars

D = Decimal


@dataclass(frozen=True)
class Costs:
    # Adverse spread + slippage allowance PER SIDE, applied to every fill.
    adverse_bps: Decimal
    fee_per_share: Decimal

    def __post_init__(self):
        if any(not isinstance(x, D) or not x.is_finite() or x < 0
               for x in (self.adverse_bps, self.fee_per_share)) or self.adverse_bps >= 10000:
            raise ValueError("finite nonnegative costs; adverse bps must be below 10000")

    def buy(self, price):
        return price * (1 + self.adverse_bps / 10000)

    def sell(self, price):
        return price * (1 - self.adverse_bps / 10000)


@dataclass(frozen=True)
class Trade:
    signal_at: str
    entry_at: str
    exit_at: str
    entry_price: Decimal
    exit_price: Decimal
    stop: Decimal
    net_pnl_per_share: Decimal
    net_r: Decimal
    reason: str


def replay(bars: tuple[Bar, ...], candidate: Candidate, costs: Costs, *,
           timeframe: str, start: int = 0, end: int | None = None,
           target_r: Decimal = D("1"), max_hold: int = 10) -> tuple[Trade, ...]:
    validate_bars(bars, timeframe)
    if not isinstance(candidate, Candidate) or (candidate in DAILY) != (timeframe == "daily"):
        raise ValueError("candidate/timeframe mismatch")
    end = len(bars) if end is None else end
    if not 0 <= start < end <= len(bars):
        raise ValueError("invalid evaluation interval")
    if not isinstance(target_r, D) or not target_r.is_finite() or target_r <= 0 or max_hold <= 0:
        raise ValueError("positive target and holding period required")
    trades = []
    pending = None
    position = None
    close_pending = False
    history = []
    for i, b in enumerate(bars[:end]):
        gap = i > 0 and (bars[i-1].closed != b.opened or bars[i-1].session_end != b.session_end)
        if timeframe == "minute" and gap:
            history = []
            pending = None
        # Signals from bars before the evaluation interval may warm indicators,
        # but cannot create a trade inside it.
        if i >= start and position is None and pending is not None:
            signal, signal_bar = pending
            pending = None
            entry = costs.buy(b.open)
            # Do not buy an opening gap through the precommitted stop.
            if b.open > signal.stop and b.volume > 0:
                risk = entry - signal.stop
                position = (entry, risk, signal.stop, i, signal_bar)
        if position is not None:
            entry, risk, stop, entry_i, signal_bar = position
            target = entry + risk * target_r
            raw_exit = None
            reason = ""
            exit_at = b.closed
            if b.open <= stop:
                raw_exit, reason, exit_at = b.open, "GAP_STOP", b.opened
            elif close_pending:
                raw_exit, reason, exit_at = b.open, "SIGNAL_EXIT", b.opened
            elif b.low <= stop:
                raw_exit, reason = stop, "STOP_FIRST"
            elif candidate not in DAILY and b.high >= target:
                # Never credit an optimistic opening gap improvement.
                raw_exit, reason = target, "TARGET"
            elif timeframe == "minute" and b.closed == b.session_end:
                raw_exit, reason = b.close, "SESSION_END"
            elif i - entry_i + 1 >= max_hold:
                # Known duration at this bar open; modeled close execution.
                raw_exit, reason = b.close, "TIME_EXIT"
            elif i == end - 1:
                raw_exit, reason = b.close, "EVALUATION_BOUNDARY"
            if raw_exit is not None:
                price = costs.sell(raw_exit)
                net = price - entry - 2 * costs.fee_per_share
                trades.append(Trade(signal_bar.closed.isoformat(), bars[entry_i].opened.isoformat(),
                                    exit_at.isoformat(), entry, price, stop, net, net / risk, reason))
                position = None
                close_pending = False
        history.append(b)
        if i < start or i == end - 1:
            continue
        completed = tuple(history)
        if position is not None:
            close_pending = exit_signal(completed, candidate)
        elif b.volume > 0 and (timeframe == "daily" or b.closed < b.session_end):
            signal = entry_signal(completed, candidate)
            pending = (signal, b) if signal else None
    return tuple(trades)


def metrics(trades: tuple[Trade, ...]) -> dict:
    all_trades = trades
    boundary_marks = tuple(t for t in trades if t.reason == "EVALUATION_BOUNDARY")
    # Ending a supplied file is not a strategy exit. Keep these marks visible
    # without manufacturing winners or losers in the headline hit rate.
    trades = tuple(t for t in trades if t.reason != "EVALUATION_BOUNDARY")
    n = len(trades)
    wins = sum(t.net_pnl_per_share > 0 for t in trades)
    losses = sum(t.net_pnl_per_share < 0 for t in trades)
    positive = sum((t.net_r for t in trades if t.net_r > 0), D(0))
    negative = -sum((t.net_r for t in trades if t.net_r < 0), D(0))
    cumulative = peak = drawdown = D(0)
    for t in trades:
        cumulative += t.net_r
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    # Descriptive binomial interval; overlapping market exposures and serial
    # correlation mean this is not a significance test or promotion decision.
    interval = None
    if n:
        p, z = wins / n, 1.959963984540054
        center = (p + z*z/(2*n)) / (1 + z*z/n)
        half = z * sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
        interval = [center - half, center + half]
    return {
        "closed_positions": n, "wins": wins, "losses": losses,
        "breakevens": n - wins - losses,
        "win_rate": wins / n if n else None,
        "descriptive_wilson_95": interval,
        "total_net_r": cumulative,
        "expectancy_r": cumulative / n if n else None,
        "profit_factor_r": positive / negative if negative else None,
        "closed_trade_drawdown_r": drawdown,
        "evaluation_boundary_marks": len(boundary_marks),
        "boundary_mark_net_r": sum((t.net_r for t in boundary_marks), D(0)),
        "trades": [asdict(t) for t in all_trades],
    }


def compare(bars: tuple[Bar, ...], costs: Costs, *, timeframe: str,
            split_at, max_hold: int = 10) -> dict:
    """Fixed candidates; explicit calendar split. No optimizer or auto-selection."""
    validate_bars(bars, timeframe)
    if split_at.utcoffset() is None:
        raise ValueError("aware split timestamp required")
    split = next((i for i, b in enumerate(bars) if b.opened >= split_at), len(bars))
    if not 0 < split < len(bars) or bars[split-1].closed > split_at:
        raise ValueError("split must separate two nonempty, nonoverlapping date ranges")
    if timeframe == "minute" and bars[split-1].session_end == bars[split].session_end:
        raise ValueError("intraday split must fall between sessions")
    candidates = tuple(c for c in Candidate if (c in DAILY) == (timeframe == "daily"))
    stress = Costs(costs.adverse_bps * 2, costs.fee_per_share * 2)
    results = []
    for c in candidates:
        for target in ((D(1),) if c in DAILY else (D("0.75"), D(1), D("1.5"))):
            kwargs = dict(timeframe=timeframe, target_r=target, max_hold=max_hold)
            development = metrics(replay(bars, c, costs, end=split, **kwargs))
            validation = metrics(replay(bars, c, costs, start=split, **kwargs))
            stressed = metrics(replay(bars, c, stress, start=split, **kwargs))
            results.append({"candidate": c.value, "target_r": target if c not in DAILY else None,
                            "development": development, "validation": validation,
                            "validation_double_cost": stressed})
    return {"scope": "OFFLINE_OHLC_SCREENING_NOT_PRODUCTION_VALIDATION",
            "execution_authorized": False, "symbol": bars[0].symbol,
            "timeframe": timeframe, "split_at": split_at.isoformat(),
            "costs_per_side": asdict(costs), "max_hold_bars": max_hold,
            "variants_tested": len(results), "results": results,
            "limitations": ["Modeled costs, not observed quotes or queue/partial-fill simulation.",
                            "Per-symbol one-unit screening, not portfolio P&L or capacity.",
                            "Validation is visible research data, not an untouched final holdout.",
                            "Wilson intervals assume independence; not proof of an edge.",
                            "Requires fresh holdout, multiple-testing controls, and PAPER forward validation.",
                            "Does not evaluate or replace current Warrior/Quick Scalper behavior."]}

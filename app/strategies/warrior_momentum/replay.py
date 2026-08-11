"""Deterministic replay aggregation and same-dataset A/B comparison."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from .models import MinuteBar, MomentumEntrySignal, ReplayReport, ReplayTrade

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def build_replay_report(
    trades: tuple[ReplayTrade, ...], *, discovered_stocks: int, setups: int, signals: int,
    slippage_samples: tuple[Decimal, ...] = (Decimal("0"), Decimal("0.01"), Decimal("0.05")),
    spread_samples: tuple[Decimal, ...] = (Decimal("0.25"), Decimal("0.50"), Decimal("1.00")),
) -> ReplayReport:
    ordered = tuple(sorted(trades, key=lambda trade: (trade.entry_time, trade.symbol)))
    wins = tuple(trade for trade in ordered if trade.pnl > 0)
    losses = tuple(trade for trade in ordered if trade.pnl < 0)
    count = len(ordered)
    gross_profit = sum((trade.pnl for trade in wins), ZERO)
    gross_loss = abs(sum((trade.pnl for trade in losses), ZERO))
    r_values = tuple(trade.r_multiple for trade in ordered)
    pnl_values = tuple(trade.pnl for trade in ordered)
    return ReplayReport(
        discovered_stocks=discovered_stocks, setups=setups, signals=signals,
        wins=len(wins), losses=len(losses),
        win_rate=ZERO if count == 0 else Decimal(len(wins)) / count * HUNDRED,
        loss_rate=ZERO if count == 0 else Decimal(len(losses)) / count * HUNDRED,
        profit_factor=None if gross_loss == 0 else gross_profit / gross_loss,
        expectancy=ZERO if count == 0 else sum(pnl_values, ZERO) / count,
        average_r=ZERO if count == 0 else sum(r_values, ZERO) / count,
        median_r=ZERO if count == 0 else Decimal(str(median(r_values))),
        max_drawdown=_max_drawdown(pnl_values),
        average_hold_seconds=ZERO if count == 0 else sum((Decimal(str((t.exit_time - t.entry_time).total_seconds())) for t in ordered), ZERO) / count,
        best_trade=max(ordered, key=lambda trade: trade.pnl, default=None),
        worst_trade=min(ordered, key=lambda trade: trade.pnl, default=None),
        average_mae_r=ZERO if count == 0 else sum((trade.mae_r for trade in ordered), ZERO) / count,
        average_mfe_r=ZERO if count == 0 else sum((trade.mfe_r for trade in ordered), ZERO) / count,
        slippage_sensitivity=tuple((sample, sum((trade.pnl - sample * trade.quantity for trade in ordered), ZERO)) for sample in slippage_samples),
        spread_sensitivity=tuple((sample, sum((trade.pnl - trade.entry_price * sample / HUNDRED * trade.quantity for trade in ordered), ZERO)) for sample in spread_samples),
        breakdowns=_breakdowns(ordered),
    )


def simulate_trade(signal: MomentumEntrySignal, future_bars: tuple[MinuteBar, ...], *, quantity: int,
                   slippage_per_share: Decimal = ZERO) -> ReplayTrade | None:
    """Conservative deterministic replay: same-bar stop wins over target."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    ordered = tuple(sorted((bar for bar in future_bars if bar.timestamp >= signal.timestamp), key=lambda bar: bar.timestamp))
    if not ordered:
        return None
    entry = signal.entry_trigger + slippage_per_share
    stop = signal.stop_price
    target = signal.target_levels[1] if len(signal.target_levels) > 1 else signal.target_levels[0]
    lows: list[Decimal] = []
    highs: list[Decimal] = []
    exit_price = ordered[-1].close - slippage_per_share
    exit_time = ordered[-1].timestamp
    for bar in ordered:
        lows.append(bar.low)
        highs.append(bar.high)
        if bar.low <= stop:
            exit_price, exit_time = stop - slippage_per_share, bar.timestamp
            break
        if bar.high >= target:
            exit_price, exit_time = target - slippage_per_share, bar.timestamp
            break
    risk = signal.risk_per_share
    pnl = (exit_price - entry) * quantity
    return ReplayTrade(
        symbol=signal.symbol, setup_type=signal.setup_type,
        catalyst_state=signal.catalyst_state, session=signal.session,
        entry_time=signal.timestamp, exit_time=exit_time,
        entry_price=entry, exit_price=exit_price, initial_risk=risk * quantity,
        quantity=quantity, r_multiple=(exit_price - entry) / risk, pnl=pnl,
        mae_r=(min(lows) - entry) / risk, mfe_r=(max(highs) - entry) / risk,
        float_shares=signal.float_shares, relative_volume=signal.relative_volume,
        momentum_score=signal.momentum_score,
    )


def _max_drawdown(pnls: tuple[Decimal, ...]) -> Decimal:
    equity = peak = drawdown = ZERO
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _bucket(value: Decimal, thresholds: tuple[Decimal, ...]) -> str:
    for threshold in thresholds:
        if value < threshold:
            return f"<{threshold}"
    return f">={thresholds[-1]}"


def _breakdowns(trades: tuple[ReplayTrade, ...]) -> tuple[tuple[str, tuple[tuple[str, Decimal], ...]], ...]:
    dimensions = {
        "setup_type": lambda t: t.setup_type.value,
        "float_bucket": lambda t: "UNKNOWN" if t.float_shares is None else _bucket(t.float_shares, (Decimal("5000000"), Decimal("10000000"), Decimal("20000000"), Decimal("50000000"))),
        "price_bucket": lambda t: _bucket(t.entry_price, (Decimal("2"), Decimal("5"), Decimal("10"), Decimal("20"))),
        "rvol_bucket": lambda t: _bucket(t.relative_volume, (Decimal("2"), Decimal("5"), Decimal("10"))),
        "catalyst_state": lambda t: t.catalyst_state.value,
        "session": lambda t: t.session,
        "momentum_score_bucket": lambda t: _bucket(t.momentum_score, (Decimal("40"), Decimal("60"), Decimal("80"))),
    }
    result = []
    for name, key_fn in dimensions.items():
        groups: dict[str, list[Decimal]] = {}
        for trade in trades:
            groups.setdefault(key_fn(trade), []).append(trade.r_multiple)
        result.append((name, tuple((key, sum(values, ZERO) / len(values)) for key, values in sorted(groups.items()))))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class StrategyComparison:
    strategy: str
    candidates: int
    trades: int
    win_rate: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal
    drawdown: Decimal
    average_risk: Decimal
    turnover: Decimal


def compare_same_dataset(current: ReplayReport, warrior: ReplayReport,
                         current_trades: tuple[ReplayTrade, ...], warrior_trades: tuple[ReplayTrade, ...]) -> tuple[StrategyComparison, StrategyComparison]:
    def row(name: str, report: ReplayReport, trades: tuple[ReplayTrade, ...]) -> StrategyComparison:
        return StrategyComparison(name, report.discovered_stocks, len(trades), report.win_rate,
                                  report.profit_factor, report.expectancy, report.max_drawdown,
                                  ZERO if not trades else sum((t.initial_risk for t in trades), ZERO) / len(trades),
                                  sum((t.entry_price * t.quantity for t in trades), ZERO))
    return row("CURRENT_ATLAS", current, current_trades), row("WARRIOR_MOMENTUM_V1", warrior, warrior_trades)


__all__ = ["build_replay_report", "simulate_trade", "StrategyComparison", "compare_same_dataset"]

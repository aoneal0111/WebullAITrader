"""Offline shared-cash daily replay. No broker, account or execution authority.

Only the frozen IBS/Donchian adaptations are supported. Aligned calendars are
required: missing symbols/dates cannot silently retain stale valuation marks.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal as D, ROUND_FLOOR

from .adaptations import Adaptation
from .candidates import entry_signal, exit_signal, validate_bars
from .replay import Costs


@dataclass(frozen=True)
class PortfolioLimits:
    initial_cash: D
    risk_fraction: D
    total_risk_fraction: D
    symbol_fraction: D
    gross_fraction: D
    max_positions: int

    def __post_init__(self):
        values = (self.initial_cash, self.risk_fraction, self.total_risk_fraction,
                  self.symbol_fraction, self.gross_fraction)
        if any(not isinstance(v, D) or not v.is_finite() or v <= 0 for v in values):
            raise ValueError('positive finite Decimal portfolio settings required')
        if (not self.risk_fraction <= self.total_risk_fraction <= 1
                or not self.symbol_fraction <= self.gross_fraction <= 1
                or type(self.max_positions) is not int or not 1 <= self.max_positions <= 32):
            raise ValueError('unlevered bounded portfolio settings required')


@dataclass
class _Position:
    quantity: int
    entry: D
    stop: D
    risk: D
    entered: int
    signal_at: str
    close_next: bool = False


def portfolio_replay(datasets, variant, costs, limits, *, start=0, end=None):
    """Recompute trades under shared capital; never filter precomputed winners.

    Alphabetical same-open entry priority is fixed and disclosed. Opening exits
    release cash before entries; intrabar and close exits cannot fund that open.
    Quantities include modeled stop liquidation costs and both fees in risk.
    """
    if not isinstance(variant, Adaptation) or not isinstance(costs, Costs) or not isinstance(limits, PortfolioLimits):
        raise ValueError('typed frozen variant, costs and portfolio limits required')
    if not isinstance(datasets, dict) or not 1 <= len(datasets) <= 32:
        raise ValueError('one to thirty-two symbol datasets required')
    symbols = sorted(datasets)
    calendar = None
    for symbol in symbols:
        bars = datasets[symbol]
        if not 1 <= len(bars) <= 5000:
            raise ValueError('bounded daily bars required')
        validate_bars(bars, 'daily')
        if symbol != bars[0].symbol:
            raise ValueError('dataset key must match symbol')
        current = tuple((b.opened, b.closed, b.session_end) for b in bars)
        if calendar is not None and current != calendar:
            raise ValueError('identical complete calendars required; no stale marks')
        calendar = current
    end = len(calendar) if end is None else end
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(calendar):
        raise ValueError('valid integer evaluation window required')
    cash = limits.initial_cash
    positions, pending = {}, {}
    trades, rejected, curve = [], [], []
    peak = limits.initial_cash
    max_dd = D(0)
    max_gross = D(0)
    max_open = 0

    def valuation(index, field):
        gross = sum((p.quantity * getattr(datasets[s][index], field) for s,p in positions.items()), D(0))
        # Liquidation equity reserves adverse sell execution and the exit fee.
        equity = cash + sum((p.quantity*(costs.sell(getattr(datasets[s][index], field))-costs.fee_per_share)
                            for s,p in positions.items()), D(0))
        return equity, gross

    def mark(index, field, stage):
        nonlocal peak, max_dd, max_gross, max_open
        equity, gross = valuation(index, field)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak-equity)/peak)
        max_gross = max(max_gross, gross/equity if equity > 0 else D(0))
        max_open = max(max_open, len(positions))
        curve.append(dict(at=(datasets[symbols[0]][index].opened if field=='open' else
                              datasets[symbols[0]][index].closed).isoformat(),
                          stage=stage,cash=cash,equity=equity,gross_notional=gross,
                          open_positions=len(positions)))
        return equity, gross

    def sell(symbol, index, raw, reason, at):
        nonlocal cash
        p = positions.pop(symbol)
        price = costs.sell(raw)
        net = (price-p.entry-2*costs.fee_per_share)*p.quantity
        cash += p.quantity*(price-costs.fee_per_share)
        trades.append(dict(symbol=symbol,quantity=p.quantity,signal_at=p.signal_at,
            entry_at=datasets[symbol][p.entered].opened.isoformat(),exit_at=at.isoformat(),
            entry_price=p.entry,exit_price=price,stop=p.stop,net_pnl=net,
            net_r=net/(p.quantity*p.risk),reason=reason))

    for i in range(start, end):
        # All open prices exist before an entry decision; future OHLC is not used.
        mark(i,'open','OPEN_BEFORE_EXITS')
        for s in sorted(tuple(positions)):
            b,p = datasets[s][i],positions[s]
            if b.open <= p.stop:
                sell(s,i,b.open,'GAP_STOP',b.opened)
            elif p.close_next:
                sell(s,i,b.open,'SIGNAL_EXIT',b.opened)
        for s in symbols:
            signal = pending.pop(s, None)
            if signal is None:
                continue
            b = datasets[s][i]
            stop,stamp = signal
            reason = None
            if s in positions:
                reason = 'DUPLICATE_POSITION'
            elif b.open <= stop:
                reason = 'GAP_THROUGH_STOP'
            elif len(positions) >= limits.max_positions:
                reason = 'POSITION_LIMIT'
            equity,gross = valuation(i,'open')
            buy = costs.buy(b.open)
            unit_cost = buy+costs.fee_per_share
            unit_risk = buy-costs.sell(stop)+2*costs.fee_per_share
            if equity <= 0:
                reason = 'NONPOSITIVE_EQUITY'
            quantity = 0
            if reason is None:
                reserved = sum((p.quantity*p.risk for p in positions.values()),D(0))
                drag = unit_cost-(costs.sell(b.open)-costs.fee_per_share)
                capacity = min(equity*limits.risk_fraction/(unit_risk+limits.risk_fraction*drag),
                    max(D(0),equity*limits.total_risk_fraction-reserved)/(unit_risk+limits.total_risk_fraction*drag),
                    equity*limits.symbol_fraction/(b.open+limits.symbol_fraction*drag),
                    max(D(0),equity*limits.gross_fraction-gross)/(b.open+limits.gross_fraction*drag),
                    max(D(0),cash)/unit_cost)
                quantity = int(capacity.to_integral_value(rounding=ROUND_FLOOR))
                if quantity < 1:
                    reason = 'INSUFFICIENT_CASH_EXPOSURE_OR_RISK_BUDGET'
            if reason is not None:
                rejected.append(dict(symbol=s,at=b.opened.isoformat(),reason=reason))
                continue
            cash -= quantity*unit_cost
            positions[s] = _Position(quantity,buy,stop,unit_risk,i,stamp)
        mark(i,'open','OPEN_AFTER_ENTRIES')
        for s in sorted(tuple(positions)):
            b,p = datasets[s][i],positions[s]
            if b.low <= p.stop:
                sell(s,i,p.stop,'STOP_FIRST',b.closed)
            elif i-p.entered+1 >= variant.max_hold:
                sell(s,i,b.close,'TIME_EXIT',b.closed)
        mark(i,'close','CLOSE')
        for s in symbols:
            completed = datasets[s][:i+1]
            if s in positions:
                positions[s].close_next = exit_signal(completed, variant.candidate)
            elif i < end-1 and datasets[s][i].volume > 0:
                signal = entry_signal(completed,variant.candidate)
                if signal is not None:
                    stop = signal.stop if variant.stop_lookback == 5 else min(
                        b.low for b in completed[-variant.stop_lookback:])*D('.9995')
                    pending[s] = (stop,datasets[s][i].closed.isoformat())
    final_equity = curve[-1]['equity']
    realized = sum((t['net_pnl'] for t in trades),D(0))
    return dict(scope='EXPLORATORY_DAILY_SHARED_CAPITAL_REPLAY',execution_authorized=False,
        portfolio_verification_complete=False,variant=asdict(variant),limits=asdict(limits),
        costs_per_side=asdict(costs),start_at=calendar[start][0].isoformat(),
        end_at=calendar[end-1][1].isoformat(),initial_cash=limits.initial_cash,
        ending_cash=cash,ending_liquidation_equity=final_equity,
        net_return=final_equity/limits.initial_cash-1,realized_net_pnl=realized,
        open_position_net_pnl=final_equity-limits.initial_cash-realized,
        sampled_equity_drawdown=max_dd,maximum_gross_to_equity=max_gross,
        maximum_positions=max_open,closed_positions=len(trades),wins=sum(t['net_pnl']>0 for t in trades),
        win_rate=sum(t['net_pnl']>0 for t in trades)/len(trades) if trades else None,
        trades=trades,rejected_entries=rejected,equity_curve=curve,
        open_positions=[dict(symbol=s,quantity=p.quantity,entry_price=p.entry,stop=p.stop,
                            entry_at=datasets[s][p.entered].opened.isoformat()) for s,p in sorted(positions.items())],
        limitations=['Explicit illustrative limits, not workstation account settings.',
            'Modeled whole-share long-only fills; no margin, leverage, settlement or volume participation model.',
            'Alphabetical same-open priority can affect results; never ranks using future profitability.',
            'Open/close liquidation equity includes costs; sampled drawdown is not intrabar worst drawdown.',
            'Gap losses may exceed initial risk and market moves may breach exposure budgets after entry.',
            'No artificial liquidation at file boundary; open inventory remains in equity and outside closed win rate.',
            'Aligned supplied calendars do not establish independently verified exchange-session completeness.',
            'Seen historical dates and assumed costs do not satisfy the portfolio review attestation.'])

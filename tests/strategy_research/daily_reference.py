"""Independent daily oracle for IBS/Donchian mechanics, not execution proof.

Deliberately does not import replay, Costs, entry_signal, exit_signal or metrics.
A trade-at-a-time traversal checks the production screen's bar-at-a-time loop.
"""
from decimal import Decimal as D


def reference_daily(bars, family, *, bps, fee, start, end, stop_days, hold_days):
    if family not in {'IBS_DAILY_REVERSION', 'DONCHIAN20_DAILY'}:
        raise ValueError('reference only supports IBS and Donchian')
    records = []
    cursor = max(start, 199)
    while cursor < end - 1:
        today = bars[cursor]
        trend = today.close > sum((b.close for b in bars[cursor-199:cursor+1]), D(0))/200
        if family == 'IBS_DAILY_REVERSION':
            width = today.high-today.low
            trigger = width > 0 and (today.close-today.low)/width < D('.2')
        else:
            trigger = today.close > max(b.high for b in bars[cursor-20:cursor])
        if not trend or not trigger or today.volume <= 0:
            cursor += 1
            continue
        stop = min(b.low for b in bars[cursor-stop_days+1:cursor+1])*D('.9995')
        entry_index = cursor+1
        opening = bars[entry_index]
        if opening.open <= stop or opening.volume <= 0:
            cursor += 1
            continue
        buy = opening.open*(D(1)+bps/D(10000))
        for day in range(entry_index, end):
            bar = bars[day]
            previous = bars[day-1]
            if family == 'IBS_DAILY_REVERSION':
                prior_exit = previous.close > bars[day-2].high
            else:
                prior_exit = previous.close < min(b.low for b in bars[day-21:day-1])
            stamp = bar.closed
            if bar.open <= stop:
                raw, reason, stamp = bar.open, 'GAP_STOP', bar.opened
            elif day > entry_index and prior_exit:
                raw, reason, stamp = bar.open, 'SIGNAL_EXIT', bar.opened
            elif bar.low <= stop:
                raw, reason = stop, 'STOP_FIRST'
            elif day-entry_index+1 >= hold_days:
                raw, reason = bar.close, 'TIME_EXIT'
            elif day == end-1:
                raw, reason = bar.close, 'EVALUATION_BOUNDARY'
            else:
                continue
            sell = raw*(D(1)-bps/D(10000))
            net = sell-buy-2*fee
            records.append(dict(signal_at=today.closed.isoformat(),
                entry_at=opening.opened.isoformat(),exit_at=stamp.isoformat(),
                entry_price=buy,exit_price=sell,stop=stop,net_pnl_per_share=net,
                net_r=net/(buy-stop),reason=reason,entry_time_precision='BAR_OPEN'))
            cursor = day
            break
    return records

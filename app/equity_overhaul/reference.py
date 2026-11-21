"""Independent session/index reference for the three frozen V1 hypotheses.

No imports of production signal, exit, replay or Costs implementations. Shared
Bar data objects are supplied by the caller. This is an offline audit, never an
order producer. Costs affect entry price, targets, exits and later entry timing.
"""
from datetime import timedelta
from decimal import Decimal as D
from itertools import groupby
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')
SPEC = {
    'ORB5_LONG_V1': (D(2), 60, 1),
    'NOISE_BAND_LONG_V1': (None, 390, 2),
    'VWAP_RECLAIM_SCALP_V1': (D(1), 5, 3),
}


def reference_trades(bars, policy, bps, fee, start, end):
    if policy not in SPEC or not D(0) <= bps < D(10000) or fee < 0:
        raise ValueError('known policy and nonnegative bounded costs required')
    if not start < end:
        raise ValueError('ordered date window required')
    days = [tuple(g) for _, g in groupby(bars, key=lambda b: b.session_end)]
    if not days:
        return []
    symbol = days[0][0].symbol
    if policy == 'NOISE_BAND_LONG_V1' and symbol not in ('SPY', 'QQQ'):
        return []
    target_r, max_hold, cap = SPEC[policy]
    buy_factor = 1 + bps / 10000
    sell_factor = 1 - bps / 10000
    next_signal_global = 0
    offset = 0
    output = []
    for day_index, candles in enumerate(days):
        trading_date = candles[0].opened.astimezone(EASTERN).date()
        if trading_date >= end:
            break
        n = len(candles)
        if trading_date < start:
            offset += n
            continue
        # Session-local cumulative VWAP; earlier sessions cannot enter it.
        total_volume = D(0); numerator = D(0); vw = []
        for b in candles:
            numerator += ((b.high+b.low+b.close)/3)*b.volume
            total_volume += b.volume
            vw.append(numerator/total_volume if total_volume else None)
        opening_high = max(b.high for b in candles[:5])
        opening_low = min(b.low for b in candles[:5])
        signals = [None]*n
        for j, b in enumerate(candles[:-1]):
            minute = b.closed.astimezone(EASTERN).hour*60+b.closed.astimezone(EASTERN).minute
            if vw[j] is None:
                continue
            if policy == 'ORB5_LONG_V1':
                if j < 5 or not 575 <= minute <= 630:
                    continue
                fires = (candles[4].close > candles[0].open and
                         candles[j-1].close <= opening_high < b.close and b.close > vw[j])
                stop = opening_low*D('.9995')
            elif policy == 'NOISE_BAND_LONG_V1':
                if day_index < 14 or (j+1)%30 or minute > 930:
                    continue
                earlier = days[day_index-14:day_index]
                if any(len(prior) <= j for prior in earlier):
                    continue
                deviation = sum((abs(prior[j].close/prior[0].open-1) for prior in earlier), D(0))/14
                ceiling = max(candles[0].open, earlier[-1][-1].close)*(1+deviation)
                fires = b.close > max(vw[j], ceiling)
                stop = min(x.low for x in candles[j-4:j+1])*D('.9995')
            else:
                if j < 21 or not 585 <= minute <= 930 or vw[j-1] is None:
                    continue
                sma = sum((x.close for x in candles[j-19:j+1]), D(0))/20
                previous_sma = sum((x.close for x in candles[j-20:j]), D(0))/20
                fires = (vw[j] >= vw[j-1] and sma > previous_sma and
                         candles[j-1].low <= vw[j-1] and
                         b.close > max(vw[j], candles[j-1].high, b.open))
                stop = min(x.low for x in candles[j-2:j+1])*D('.9995')
            if fires and 0 < stop < b.close:
                signals[j] = stop
        entries = 0
        decision = max(0, next_signal_global-offset)
        # Find a completed signal, then walk its trade independently to the
        # first eligible exit. Resume signal search after the frozen cooldown.
        while decision < n-1 and entries < cap:
            stop = signals[decision]
            if stop is None:
                decision += 1
                continue
            entry_index = decision+1
            first = candles[entry_index]
            entry = first.open*buy_factor
            if first.open <= stop or entry <= stop:
                decision = entry_index
                continue
            entries += 1
            risk = entry-stop
            target = entry+target_r*risk if target_r is not None else None
            waiting_exit = False
            for k in range(entry_index, n):
                b = candles[k]
                raw = None; reason = None; exited_at = b.closed
                if b.open <= stop:
                    raw = b.open; reason = 'GAP_STOP'; exited_at = b.opened
                elif waiting_exit:
                    raw = b.open; reason = 'SIGNAL_EXIT'; exited_at = b.opened
                elif b.low <= stop:
                    raw = stop; reason = 'STOP_FIRST'
                elif target is not None and b.high >= target:
                    raw = target; reason = 'TARGET'
                elif k == n-1:
                    raw = b.close; reason = 'SESSION_END'
                elif k-entry_index+1 >= max_hold:
                    raw = b.close; reason = 'TIME_EXIT'
                if raw is not None:
                    sold = raw*sell_factor; net = sold-entry-2*fee
                    output.append({'policy_id': policy, 'symbol': symbol,
                        'signal_at': candles[decision].closed.isoformat(),
                        'entry_at': first.opened.isoformat(), 'exit_at': exited_at.isoformat(),
                        'entry': str(entry), 'exit': str(sold), 'stop': str(stop),
                        'net_pnl_per_share': str(net), 'net_r': str(net/risk), 'reason': reason})
                    next_signal_global = offset+k+3
                    decision = k+3
                    break
                if policy == 'NOISE_BAND_LONG_V1' and (k+1)%30 == 0:
                    waiting_exit = signals[k] is None
            else:
                raise ValueError('reference could not resolve position inside session')
        offset += n
    return output

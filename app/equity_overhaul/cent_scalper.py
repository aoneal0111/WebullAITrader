"""Disconnected quote-replay candidate; no broker, desktop or order authority.

A quote lift is a research signal, never proof that more upside remains.
All fills below are hypothetical marketable fills with explicit costs.
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal as D


@dataclass(frozen=True)
class Quote:
    symbol: str
    received_at: datetime
    quoted_at: datetime
    bid: D
    ask: D
    bid_size: D
    ask_size: D
    regular_session: bool
    tradable: bool = True
    halted: bool = False

    def __post_init__(self):
        if not self.symbol or len(self.symbol) > 32:
            raise ValueError('bounded symbol required')
        for t in (self.received_at, self.quoted_at):
            if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None:
                raise ValueError('aware timestamps required')
        for p in (self.bid, self.ask, self.bid_size, self.ask_size):
            if not isinstance(p, D) or not p.is_finite() or p <= 0:
                raise ValueError('positive finite Decimal quote and sizes required')
        if self.ask < self.bid:
            raise ValueError('crossed quote')
        if any(type(x) is not bool for x in (self.regular_session, self.tradable, self.halted)):
            raise ValueError('explicit boolean market states required')


@dataclass(frozen=True)
class Rules:
    target: D = D('.05')
    stop: D = D('.05')
    slippage: D = D('.005')
    fee_per_share: D = D('.0035')
    minimum_fee: D = D('0')
    shares: int = 10
    max_hold_seconds: int = 60
    cooldown_seconds: int = 10

    def __post_init__(self):
        for p in (self.target, self.stop, self.slippage, self.fee_per_share, self.minimum_fee):
            if not isinstance(p, D) or not p.is_finite() or p < 0:
                raise ValueError('finite nonnegative Decimal rules required')
        if self.target not in (D('.05'), D('.10')) or self.stop <= 0:
            raise ValueError('frozen 5/10 cent target and positive stop required')
        if any(type(v) is not int or v <= 0 for v in (
                self.shares, self.max_hold_seconds, self.cooldown_seconds)):
            raise ValueError('positive integer size/time bounds required')


def replay(quotes, rules=Rules()):
    """One symbol per replay, at most 100,000 input quotes, bounded memory.

    Three rising BID *and* ASK updates over 1..3 seconds arm a next-quote
    entry. No HOD, Warrior signal, completed candle, or 10% gainer required.
    Entry is cancelled on >2s data gaps, stale/unsafe/session-end evidence.
    Profit is taken by crossing BID after it covers target and all costs;
    this deliberately does not assume that a resting limit touch is filled.
    """
    window = deque(maxlen=3)
    pending = position = last = symbol = None
    cooldown_until = None
    trades = []
    ignored = cancelled = gaps = signals = 0
    fee = max(rules.minimum_fee, rules.fee_per_share * rules.shares)
    for count, q in enumerate(quotes, 1):
        if count > 100000:
            raise ValueError('input quote bound exceeded')
        if not isinstance(q, Quote):
            raise ValueError('typed quotes required')
        if symbol is None:
            symbol = q.symbol
        if q.symbol != symbol:
            raise ValueError('one symbol per replay; no hidden shared capital assumption')
        if last is not None and (q.received_at <= last.received_at or q.quoted_at <= last.quoted_at):
            raise ValueError('strictly increasing receipt and provider times required')
        gap = last is not None and (q.quoted_at-last.quoted_at).total_seconds() > 2
        gaps += int(gap)
        last = q
        fresh = 0 <= (q.received_at-q.quoted_at).total_seconds() <= 2
        usable = fresh and q.tradable and not q.halted
        if gap or not usable or not q.regular_session:
            window.clear()
            cancelled += int(pending is not None)
            pending = None
        if not usable:
            ignored += 1
            continue
        if position is not None:
            entry, at, stop_price = position
            exit_price = q.bid-rules.slippage
            net = (exit_price-entry)*rules.shares-2*fee
            reason = ('STOP' if q.bid <= stop_price else
                      'SESSION_EXIT' if not q.regular_session else
                      'TIME_EXIT' if (q.received_at-at).total_seconds() >= rules.max_hold_seconds else
                      'TARGET' if net >= rules.target*rules.shares else None)
            if reason:
                trades.append(dict(entry_at=at.isoformat(), exit_at=q.received_at.isoformat(),
                    reason=reason, net=net, exit_after_gap=gap))
                position = None
                cooldown_until = q.received_at.timestamp()+rules.cooldown_seconds
                window.clear()
            continue
        if not q.regular_session:
            continue
        if pending is not None:
            # Signal prices cannot be filled retroactively. Cancel if the next
            # quote has already consumed the entire targeted move or reversed.
            signal_ask, signal_bid = pending
            pending = None
            if (q.ask-signal_ask >= rules.target or q.bid < signal_bid or
                    min(q.ask_size, q.bid_size) < rules.shares):
                cancelled += 1
                window.clear()
                continue
            entry = q.ask+rules.slippage
            position = (entry, q.received_at, entry-rules.stop)
            window.clear()
            continue
        if cooldown_until is not None and q.received_at.timestamp() < cooldown_until:
            continue
        window.append(q)
        if len(window) != 3:
            continue
        a, b, c = window
        elapsed = (c.quoted_at-a.quoted_at).total_seconds()
        spread_cost = c.ask-c.bid+2*rules.slippage+2*fee/rules.shares
        if (1 <= elapsed <= 3 and a.bid < b.bid < c.bid and a.ask < b.ask < c.ask
                and c.bid-a.bid >= D('.01')
                and spread_cost < rules.target
                and min(c.ask_size, c.bid_size) >= rules.shares):
            signals += 1
            pending = (c.ask, c.bid)
            window.clear()
    mark = None
    if position is not None:
        # Only mark against valid, fresh evidence; never fabricate liquidation.
        mark = ((last.bid-rules.slippage-position[0])*rules.shares-2*fee
                if usable else None)
    return dict(scope='OFFLINE_CENT_SCALPER_CANDIDATE_V1', execution_authorized=False,
        orders=0, baseline_modified=False, signals=signals, cancelled_entries=cancelled,
        ignored_quotes=ignored, data_gaps=gaps, closed_trades=len(trades),
        wins=sum(t['net'] > 0 for t in trades),
        net=sum((t['net'] for t in trades), D('0')), trades=trades,
        open_positions=int(position is not None), open_net_mark=mark,
        pending_entries=int(pending is not None), qualified=False)

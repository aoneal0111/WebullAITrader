"""Offline paired Momentum exit accounting, with no strategy or order adapter."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal as D

ZERO = D(0)
MODES = ('FULL_2R', 'HALF_1R_HALF_2R')


def positive(value, *, zero=False):
    if not isinstance(value,D) or not value.is_finite() or (value<0 if zero else value<=0):
        raise ValueError('FINITE_DECIMAL_REQUIRED')
    return value


def aware(value):
    return isinstance(value,datetime) and value.tzinfo is not None and value.utcoffset() is not None


@dataclass(frozen=True)
class Candle:
    opened: datetime
    closed: datetime
    open: D
    high: D
    low: D
    close: D


@dataclass(frozen=True)
class Costs:
    adverse_bps_per_side: D = ZERO
    commission_per_share: D = ZERO
    minimum_commission_per_order: D = ZERO

    def __post_init__(self):
        for value in (self.adverse_bps_per_side,self.commission_per_share,self.minimum_commission_per_order):
            positive(value,zero=True)
        if self.adverse_bps_per_side>=10000:
            raise ValueError('INVALID_SLIPPAGE')

    def fee(self, quantity):
        return max(self.commission_per_share*quantity,self.minimum_commission_per_order)


def validate_path(candles, session_end):
    if not aware(session_end) or not 1<=len(candles)<=390:
        raise ValueError('BOUNDED_SESSION_PATH_REQUIRED')
    previous=None
    for b in candles:
        if (not isinstance(b,Candle) or not aware(b.opened) or not aware(b.closed)
                or b.opened.second or b.opened.microsecond
                or b.closed-b.opened!=timedelta(minutes=1)
                or b.closed>session_end
                or (previous is not None and b.opened!=previous)):
            raise ValueError('CONTIGUOUS_AWARE_MINUTES_REQUIRED')
        for value in (b.open,b.high,b.low,b.close):positive(value)
        if not b.low<=min(b.open,b.close)<=max(b.open,b.close)<=b.high:
            raise ValueError('INVALID_OHLC')
        previous=b.closed
    if candles[-1].closed!=session_end or session_end-candles[0].opened>timedelta(minutes=390):
        raise ValueError('PATH_MUST_REACH_DECLARED_SESSION_END')


def replay_exit_path(candles, *, session_end, entry_trigger, initial_stop,
                     quantity, mode, costs=Costs()):
    """Compare management of an already-armed first-bar trigger, not entry selection.

    Every bar through declared session end is required, even if an early exit
    would have survived a missing later bar. Both policies therefore use the
    same coverage cohort. Trigger and stop must be fixed before this path.
    """
    candles=tuple(candles)
    validate_path(candles,session_end)
    positive(entry_trigger);positive(initial_stop)
    if type(quantity) is not int or quantity<=0 or quantity>1_000_000:
        raise ValueError('BOUNDED_WHOLE_SHARE_QUANTITY_REQUIRED')
    if mode not in MODES or not isinstance(costs,Costs) or initial_stop>=entry_trigger:
        raise ValueError('FIXED_POLICY_AND_POSITIVE_RISK_REQUIRED')
    first=candles[0]
    if first.open<=initial_stop or first.high<entry_trigger:
        return dict(status='NO_ENTRY',reason='GAP_THROUGH_STOP' if first.open<=initial_stop else 'TRIGGER_NOT_TOUCHED',
                    execution_authorized=False,orders=0,mode=mode,quantity=quantity)
    raw_entry=max(first.open,entry_trigger)
    buy=1+costs.adverse_bps_per_side/10000
    sell=1-costs.adverse_bps_per_side/10000
    entry_fill=raw_entry*buy; risk=entry_fill-initial_stop
    target1=entry_fill+risk;target2=entry_fill+2*risk
    partial_quantity=quantity//2 if mode=='HALF_1R_HALF_2R' else 0
    remaining=quantity; partial_index=None; stop=initial_stop
    fills=[dict(side='BUY',quantity=quantity,price=entry_fill,fee=costs.fee(quantity),
                reason='ARMED_TRIGGER',bar_opened=first.opened.isoformat())]
    def exit_fill(qty,raw,reason,b):
        fills.append(dict(side='SELL',quantity=qty,price=raw*sell,fee=costs.fee(qty),
                          reason=reason,bar_opened=b.opened.isoformat()))
    for index,b in enumerate(candles):
        if partial_index is not None and index>partial_index:
            stop=raw_entry  # Price breakeven only; commissions/slippage still apply.
        if b.open<=stop or b.low<=stop:
            reason='GAP_STOP' if b.open<=stop else 'STOP_FIRST'
            exit_fill(remaining,min(b.open,stop),reason,b)
            remaining=0
        else:
            if partial_quantity and partial_index is None and b.high>=target1:
                exit_fill(partial_quantity,target1,'PARTIAL_1R',b)
                remaining-=partial_quantity;partial_index=index
            if b.high>=target2:
                exit_fill(remaining,target2,'TARGET_2R',b);remaining=0
            elif index==len(candles)-1:
                exit_fill(remaining,b.close,'SESSION_END',b);remaining=0
        if not remaining:break
    exit_notional=sum((x['price']*x['quantity'] for x in fills if x['side']=='SELL'),ZERO)
    entry_notional=entry_fill*quantity
    fees=sum((x['fee'] for x in fills),ZERO)
    gross=exit_notional-entry_notional
    return dict(scope='OFFLINE_WHOLE_SHARE_MOMENTUM_EXIT_PATH_V1',status='CLOSED',mode=mode,
        quantity=quantity,partial_quantity=partial_quantity,partial_taken=partial_index is not None,
        one_share_partial_fallback=mode=='HALF_1R_HALF_2R' and partial_quantity==0,
        entry_fill=entry_fill,initial_stop=initial_stop,planned_initial_price_risk=risk*quantity,
        gross_pnl=gross,commissions=fees,net_pnl=gross-fees,net_r=(gross-fees)/(risk*quantity),
        fills=fills,remaining_quantity=remaining,orders=0,execution_authorized=False,
        limitations=['Invented stop/limit fills; no spread, queue, liquidity, halt or latency evidence',
                     'Intrabar times are unknown; bar_opened labels the interval, not an execution time',
                     'Stop wins an entry-bar or target-bar ambiguity; raised stop starts next bar',
                     'Adverse bps may price fills beyond OHLC; it is an explicit cost stress assumption',
                     'Declared session end is caller supplied, not independently calendar verified'])


def compare_exit_paths(candles, **kwargs):
    if 'mode' in kwargs:
        raise ValueError('PAIRED_COMPARISON_SETS_BOTH_FIXED_MODES')
    frozen=tuple(candles)
    return dict(scope='OFFLINE_PAIRED_WHOLE_SHARE_MOMENTUM_EXITS_V1',orders=0,
        execution_authorized=False,promotion='NONE',
        policies=[replay_exit_path(frozen,mode=mode,**kwargs) for mode in MODES])

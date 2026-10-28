"""Disconnected cent-scalp portfolio experiment on observed quotes only."""
from collections import Counter,deque
from dataclasses import dataclass
from decimal import Decimal as D

from .cent_scalper import Quote


@dataclass(frozen=True)
class Protocol:
    target: D=D('.05')
    stop: D=D('.05')
    slippage: D=D('.005')
    fee_per_share: D=D('.0035')
    wallet: D=D('1000')
    max_notional: D=D('500')
    max_spread: D=D('.01')
    max_positions: int=2
    max_shares: int=10
    book_participation: D=D('.10')
    max_hold: int=60
    cooldown: int=10
    session_loss: D=D('20')
    max_entries_symbol_hour: int=6

    def __post_init__(self):
        for name in ('target','stop','slippage','fee_per_share','wallet','max_notional','max_spread','book_participation','session_loss'):
            if not isinstance(getattr(self,name),D) or not getattr(self,name).is_finite() or getattr(self,name)<0:
                raise ValueError('FINITE_DECIMAL_PROTOCOL_REQUIRED')
        for name in ('max_positions','max_shares','max_hold','cooldown','max_entries_symbol_hour'):
            if type(getattr(self,name)) is not int or getattr(self,name)<=0:raise ValueError('INTEGER_PROTOCOL_REQUIRED')
        if self.target not in (D('.05'),D('.10')) or self.slippage not in (D('.005'),D('.01')):
            raise ValueError('FROZEN_TARGET_AND_SLIPPAGE_CASE_REQUIRED')
        # All other settings are fixed; no per-symbol or outcome-based tuning.
        for name,value in {'stop':D('.05'),'fee_per_share':D('.0035'),'wallet':D(1000),
            'max_notional':D(500),'max_spread':D('.01'),'max_positions':2,'max_shares':10,
            'book_participation':D('.10'),'max_hold':60,'cooldown':10,'session_loss':D(20),
            'max_entries_symbol_hour':6}.items():
            if getattr(self,name)!=value:raise ValueError('FROZEN_PORTFOLIO_PROTOCOL_REQUIRED')


class Portfolio:
    """One shared wallet. Quote/time arguments must be causal journal arrivals.

    Eligibility gates entries only. Protective sells use the next fresh valid
    bid even when an entry lease has expired, and are never forced at a target.
    """
    def __init__(self,rules):
        self.rules=rules;self.cash=rules.wallet;self.realized=D(0)
        self.positions={};self.windows={};self.pending={};self.last={};self.cooldown={};self.attempts={}
        self.trades=[];self.counts=Counter();self.peak=rules.wallet;self.drawdown=D(0);self.last_tick=None;self.unmarkable=set()

    def invalidate(self,symbol):
        self.pending.pop(symbol,None);self.windows.pop(symbol,None)

    def feed(self,q,tick,sequence,*,eligible,session,halted=False):
        if not isinstance(q,Quote) or type(eligible) is not bool or type(halted) is not bool:
            raise ValueError('TYPED_CAUSAL_QUOTES_REQUIRED')
        if self.last_tick is not None and tick<self.last_tick:raise ValueError('JOURNAL_MONOTONIC_TIME_REGRESSED')
        self.last_tick=tick;self.counts['quotes']+=1
        halted=halted or q.halted;eligible=eligible and q.tradable
        if halted:self.unmarkable.add(q.symbol);self.invalidate(q.symbol)
        age=(q.received_at-q.quoted_at).total_seconds()
        if age<0:
            self.invalidate(q.symbol);self.counts['future_quote']+=1;return
        previous=self.last.get(q.symbol)
        if previous is not None and q.quoted_at<=previous[0].quoted_at:
            self.counts['duplicate_or_older_provider_quote']+=1;return
        if len(self.last)>=10000 and q.symbol not in self.last:raise ValueError('SYMBOL_BOUND_EXCEEDED')
        self.last[q.symbol]=(q,tick)
        if halted:self.unmarkable.add(q.symbol)
        else:self.unmarkable.discard(q.symbol)
        fresh=0<=age<=2
        gap=previous is not None and (q.quoted_at-previous[0].quoted_at).total_seconds()>2
        self.counts['provider_gaps']+=int(gap)
        if not fresh or halted:
            self.invalidate(q.symbol);self.counts['stale_future_or_halted']+=1;return
        position=self.positions.get(q.symbol)
        if position is not None:
            per_share=q.bid-self.rules.slippage-position['entry']-2*self.rules.fee_per_share
            reason=position['exit_reason'] or ('STOP' if q.bid<=position['entry']-self.rules.stop else
                'SESSION_EXIT' if session!=position['session'] else
                'TIME_EXIT' if tick-position['tick']>=self.rules.max_hold else
                'TARGET' if per_share>=self.rules.target else None)
            if reason:
                quantity=min(position['remaining'],int(q.bid_size))
                if quantity>0:
                    self.cash+=(q.bid-self.rules.slippage-self.rules.fee_per_share)*quantity
                    net=per_share*quantity;self.realized+=net;position['net']+=net
                    position['remaining']-=quantity;position['exit_reason']=reason
                    self.counts['simulated_exit_fills']+=1
                    if position['remaining']>0:self.unmarkable.add(q.symbol)
                    if position['remaining']==0:
                        self.trades.append(dict(symbol=q.symbol,entry_sequence=position['sequence'],exit_sequence=sequence,
                            entry_at=position['at'],exit_at=q.received_at.isoformat(),quantity=position['quantity'],
                            entry_fill=position['entry'],last_exit_fill=q.bid-self.rules.slippage,
                            net=position['net'],reason=reason,session=position['session'],exit_after_gap=gap))
                        del self.positions[q.symbol];self.cooldown[q.symbol]=tick+self.rules.cooldown
                        self.invalidate(q.symbol)
                else:self.counts['exit_no_displayed_size']+=1
            self._mark(tick);return
        self._mark(tick)
        if gap:self.invalidate(q.symbol)
        reason=('UNKNOWN_OR_INELIGIBLE_STATE' if not eligible else
            'SESSION_CLOSED' if session not in ('PREMARKET','CORE','AFTER_HOURS') else
            'PRICE_OR_SPREAD' if q.ask<D(1) or not D(0)<q.ask-q.bid<=self.rules.max_spread else
            'SESSION_LOSS_LIMIT' if self.realized<=-self.rules.session_loss else
            'TRADE_RECORD_LIMIT' if len(self.trades)>=5000 else
            'SYMBOL_COOLDOWN' if tick<self.cooldown.get(q.symbol,-1) else None)
        if reason:
            self.invalidate(q.symbol);self.counts[reason]+=1;return
        pending=self.pending.pop(q.symbol,None)
        if pending is not None:
            if (session!=pending['session'] or tick-pending['tick']>2
                    or q.ask-pending['ask']>=self.rules.target or q.bid<pending['bid']):
                self.invalidate(q.symbol);self.counts['pending_cancelled']+=1;return
            attempts=self.attempts.setdefault(q.symbol,deque())
            while attempts and tick-attempts[0]>=3600:attempts.popleft()
            if len(attempts)>=self.rules.max_entries_symbol_hour:
                self.counts['SYMBOL_HOURLY_LIMIT']+=1;return
            if len(self.positions)>=self.rules.max_positions:
                self.counts['SHARED_POSITION_LIMIT']+=1;return
            entry=q.ask+self.rules.slippage
            unit=entry+self.rules.fee_per_share
            quantity=min(self.rules.max_shares,int(self.cash/unit),int(self.rules.max_notional/unit),
                int(min(q.bid_size,q.ask_size)*self.rules.book_participation))
            if quantity<=0:self.counts['CASH_OR_DISPLAYED_SIZE']+=1;return
            self.cash-=unit*quantity;attempts.append(tick)
            self.positions[q.symbol]=dict(entry=entry,tick=tick,at=q.received_at.isoformat(),sequence=sequence,
                quantity=quantity,remaining=quantity,net=D(0),exit_reason=None,session=session)
            self.counts['simulated_entry_fills']+=1;self.invalidate(q.symbol);self._mark(tick);return
        window=self.windows.setdefault(q.symbol,deque(maxlen=3));window.append(q)
        if len(window)<3:return
        a,b,c=window;elapsed=(c.quoted_at-a.quoted_at).total_seconds()
        cost=c.ask-c.bid+2*self.rules.slippage+2*self.rules.fee_per_share
        if (1<=elapsed<=3 and a.bid<b.bid<c.bid and a.ask<b.ask<c.ask
                and c.bid-a.bid>=D('.01') and cost<self.rules.target
                and min(c.bid_size,c.ask_size)*self.rules.book_participation>=1):
            self.pending[q.symbol]=dict(ask=c.ask,bid=c.bid,tick=tick,session=session)
            self.counts['signals']+=1;window.clear()

    def _mark(self,tick):
        # Mark drawdown only when every open holding has fresh observable BBO.
        value=self.cash
        for symbol,p in self.positions.items():
            if symbol in self.unmarkable:return None
            last=self.last.get(symbol)
            if last is None:return None
            q,at=last
            if q.bid_size<p['remaining']:return None
            if tick-at>2 or not 0<=(q.received_at-q.quoted_at).total_seconds()<=2:return None
            value+=(q.bid-self.rules.slippage-self.rules.fee_per_share)*p['remaining']
        self.peak=max(self.peak,value);self.drawdown=max(self.drawdown,self.peak-value)
        return value

    def result(self):
        closed=sum((t['net'] for t in self.trades),D(0));wins=sum(t['net']>0 for t in self.trades)
        losses=sum(t['net']<0 for t in self.trades)
        gain=sum((t['net'] for t in self.trades if t['net']>0),D(0))
        loss=-sum((t['net'] for t in self.trades if t['net']<0),D(0))
        return dict(closed_trades=len(self.trades),wins=wins,losses=losses,
            win_rate=D(wins)/len(self.trades) if self.trades else None,closed_net=closed,
            realized_net_including_partial=self.realized,ending_cash=self.cash,
            current_liquidation_mark=None if self.last_tick is None else self._mark(self.last_tick),
            observed_marked_drawdown=self.drawdown,profit_factor=gain/loss if loss else None,
            open_positions=[dict(symbol=s,**p) for s,p in self.positions.items()],
            pending_signals=len(self.pending),counts=dict(self.counts),trades=self.trades,
            execution_authorized=False,orders=0,qualified=False)

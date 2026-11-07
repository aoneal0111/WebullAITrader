"""Causal one-position OHLC replay of the frozen replacement candidates."""
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from app.strategy_research.candidates import validate_bars
from app.strategy_research.replay import Costs
from .strategies import RULES, signal, exit_due, rule_fingerprint


def replay(bars,policy,costs,*,evaluate_from=None,evaluate_until=None):
    validate_bars(bars,'minute')
    if policy not in RULES or not isinstance(costs,Costs):raise ValueError('fixed policy and costs required')
    # Half-open session-date windows. Earlier sessions warm indicators but may
    # neither submit a signal nor carry a position into the evaluation window.
    from datetime import date
    if any(x is not None and type(x) is not date for x in (evaluate_from,evaluate_until)):
        raise ValueError('evaluation boundaries must be dates')
    if evaluate_from is not None and evaluate_until is not None and evaluate_from>=evaluate_until:
        raise ValueError('ordered evaluation boundaries required')
    history=[];pending=None;position=None;trades=[];counts={};close_pending=False;cooldown=0
    for i,b in enumerate(bars):
        trading_date=b.opened.astimezone(ZoneInfo('America/New_York')).date()
        if evaluate_until is not None and trading_date>=evaluate_until:break
        eligible=evaluate_from is None or trading_date>=evaluate_from
        if i and bars[i-1].session_end!=b.session_end:
            if position:raise ValueError('position must close within original session')
            pending=None;close_pending=False
            if policy != "NOISE_BAND_LONG_V1":history=[]
            elif history:
                # Trim once per session, not by rescanning up to 8,580 bars
                # after every minute in a multi-year replay.
                keep=sorted({x.session_end for x in history})[-21:]
                history=[x for x in history if x.session_end>=keep[0]]
        day=b.session_end.isoformat()
        exited=False
        if position is None and pending is not None and counts.get(day,0)<RULES[policy]['max_trades'] and i>=cooldown:
            s=pending
            entry=costs.buy(b.open)
            if b.open>s.stop and entry>s.stop:
                # Freeze stop and target before seeing the entry bar range.
                risk=entry-s.stop
                position=(s,entry,risk,i)
                counts[day]=counts.get(day,0)+1
            pending=None
        if position:
            s,entry,risk,entry_i=position
            raw=reason=None;exited_at=b.closed
            target=entry+s.target_r*risk if s.target_r is not None else None
            if b.open<=s.stop:raw,reason,exited_at=b.open,'GAP_STOP',b.opened
            elif close_pending:raw,reason,exited_at=b.open,'SIGNAL_EXIT',b.opened
            elif b.low<=s.stop:raw,reason=s.stop,'STOP_FIRST'
            elif target is not None and b.high>=target:raw,reason=target,'TARGET'
            elif b.closed==b.session_end:raw,reason=b.close,'SESSION_END'
            elif i-entry_i+1>=s.max_hold:raw,reason=b.close,'TIME_EXIT'
            if raw is not None:
                fill=costs.sell(raw);net=fill-entry-2*costs.fee_per_share
                trades.append(dict(policy_id=policy,symbol=b.symbol,signal_at=s.at.isoformat(),
                    entry_at=bars[entry_i].opened.isoformat(),exit_at=exited_at.isoformat(),
                    entry=str(entry),exit=str(fill),stop=str(s.stop),net_r=str(net/risk),
                    net_pnl_per_share=str(net),reason=reason,rule_fingerprint=rule_fingerprint(policy)))
                position=None;close_pending=False;exited=True;cooldown=i+3
        history.append(b)
        # At most 21 complete previous sessions plus the current session.
        if position:
            local=b.closed.astimezone(ZoneInfo('America/New_York'))
            if policy=='NOISE_BAND_LONG_V1' and (local.hour*60+local.minute-570)%30==0:
                close_pending=exit_due(tuple(history),policy,at=b.closed)
        elif eligible and not exited and b.closed<b.session_end and i>=cooldown and counts.get(day,0)<RULES[policy]['max_trades']:
            local=b.closed.astimezone(ZoneInfo('America/New_York'))
            minute=local.hour*60+local.minute
            due=(policy!='NOISE_BAND_LONG_V1' or (minute-570)%30==0)
            due=due and (policy!='ORB5_LONG_V1' or minute<=630)
            if due:pending=signal(tuple(history),policy,at=b.closed)
    # Input validator requires complete sessions; still never manufacture a
    # final winning close if a caller changes that requirement in the future.
    gains=sum((max(D(t['net_r']),D(0)) for t in trades),D(0))
    losses=-sum((min(D(t['net_r']),D(0)) for t in trades),D(0))
    wins=sum(D(t['net_r'])>0 for t in trades)
    return dict(policy_id=policy,closed_trades=len(trades),wins=wins,
        win_rate=str(D(wins)/len(trades)) if trades else None,
        profit_factor=str(gains/losses) if losses else None,
        expectancy_r=str(sum((D(t['net_r']) for t in trades),D(0))/len(trades)) if trades else None,
        unresolved_positions=int(position is not None),trades=trades,execution_authorized=False)

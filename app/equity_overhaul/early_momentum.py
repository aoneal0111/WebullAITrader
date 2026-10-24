"""Pre-HOD pullback and partial-profit research; no production order adapter."""
from decimal import Decimal as D
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')

POLICY = 'PRE_HOD_PULLBACK_10PCT_V1'
SPEC = dict(owner='WARRIOR_MOMENTUM', watch='Known session high >= prior complete regular close * 1.10',
    entry='After a rising three-bar impulse, 1-3 lower-high non-green pause bars; arm one-bar stop at last pause high + .01 below known HOD',
    stop='Pause low minus .01, fixed before entry', session='Regular bars only; decisions 09:35-15:30 ET',
    management=['FULL_2R','HALF_1R_HALF_2R'], max_entries_per_symbol_session=1,
    partial='Half at 1R; remainder stop to raw entry starting next minute; remainder target 2R',
    ambiguity='Known stop before target if same candle; no retroactive same-bar breakeven stop',
    execution_authorized=False, promotion='NONE')


def specification():
    return json.loads(json.dumps(SPEC))


def fingerprint():
    return sha256(json.dumps(SPEC,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def setup(bars, j, prior_close, *, known_high=None):
    """Decision uses bars through j only; future candle may trigger the resting order."""
    if j < 4 or not prior_close or prior_close<=0: return None
    b=bars[j];local=b.closed.astimezone(EASTERN)
    minute=local.hour*60+local.minute
    if not 575<=minute<=930 or b.closed>=b.session_end: return None
    hod=known_high if known_high is not None else max(x.high for x in bars[:j+1])
    if hod < prior_close*D('1.10'): return None
    for length in (1,2,3):
        first=j-length+1
        if first<3:continue
        impulse=bars[first-3:first];pause=bars[first:j+1]
        if not (impulse[0].close<impulse[1].close<impulse[2].close and impulse[-1].high==hod):continue
        if any(x.close>x.open for x in pause):continue
        if any(x.high>=previous.high for previous,x in zip((impulse[-1],)+tuple(pause[:-1]),pause)):continue
        trigger=pause[-1].high+D('.01');stop=min(x.low for x in pause)-D('.01')
        if 0<stop<trigger<hod:
            return dict(trigger=trigger,stop=stop,hod=hod,signal_at=b.closed.isoformat(),pause_bars=length)
    return None


def replay(prepared, mode, bps, fee, start, end):
    if mode not in SPEC['management'] or not start<end or not D(0)<=bps<D(10000) or fee<0:
        raise ValueError('fixed early-entry research specification required')
    result=[];diag=dict(symbol_sessions=0,ten_percent_watch_sessions=0,armed_decisions=0,
        gap_entries_at_or_above_hod=0,entry_gap_through_stop=0)
    buy=1+bps/10000;sell=1-bps/10000
    for symbol,days in prepared.items():
        for day,s in days.items():
            if not start<=day<end:continue
            diag['symbol_sessions']+=1
            if not s['prior14']:continue
            prior=s['prior14'][-1]['bars'][-1].close;bars=s['bars']
            if max(b.high for b in bars)>=prior*D('1.10'):diag['ten_percent_watch_sessions']+=1
            known_high=max(b.high for b in bars[:4])
            for j in range(4,len(bars)-1):
                known_high=max(known_high,bars[j].high)
                signal=setup(bars,j,prior,known_high=known_high)
                if signal is None:continue
                diag['armed_decisions']+=1;entrybar=bars[j+1]
                if entrybar.open>=signal['hod']:
                    diag['gap_entries_at_or_above_hod']+=1;continue
                if entrybar.open<=signal['stop']:
                    diag['entry_gap_through_stop']+=1;continue
                if entrybar.high<signal['trigger']:continue
                raw_entry=max(entrybar.open,signal['trigger']);entry=raw_entry*buy
                # Execution may be adverse; skip if assumed fill is already at HOD.
                if entry>=signal['hod']:
                    diag['gap_entries_at_or_above_hod']+=1;continue
                initial_stop=signal['stop'];risk=entry-initial_stop
                target1=entry+risk;target2=entry+2*risk;remaining=D(1);cash=D(0);stop=initial_stop
                partial_at=None;reason=None
                for k in range(j+1,len(bars)):
                    b=bars[k]
                    if partial_at is not None and k>partial_at:stop=raw_entry
                    if b.open<=stop or b.low<=stop:
                        price=min(b.open,stop);cash+=remaining*(price*sell-fee);remaining=D(0)
                        reason='GAP_STOP' if b.open<=stop else 'STOP_FIRST';exit_at=b.opened if b.open<=stop else b.closed
                    else:
                        # Conservative limit fills at target, no favorable open-gap improvement.
                        if mode=='HALF_1R_HALF_2R' and partial_at is None and b.high>=target1:
                            cash+=D('.5')*(target1*sell-fee);remaining-=D('.5');partial_at=k
                        if b.high>=target2:
                            cash+=remaining*(target2*sell-fee);remaining=D(0);reason='TARGET_2R';exit_at=b.closed
                        elif k==len(bars)-1:
                            cash+=remaining*(b.close*sell-fee);remaining=D(0);reason='SESSION_END';exit_at=b.closed
                    if remaining==0:break
                net=cash-entry-fee
                result.append(dict(policy_id=POLICY,owner=SPEC['owner'],mode=mode,symbol=symbol,
                    signal_at=signal['signal_at'],entry_at=entrybar.opened.isoformat(),exit_at=exit_at.isoformat(),
                    entry=str(entry),stop=str(initial_stop),known_hod=str(signal['hod']),net_r=str(net/risk),
                    net_pnl_per_share=str(net),reason=reason,partial_taken=partial_at is not None))
                break
    return result,diag

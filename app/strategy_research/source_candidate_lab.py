"""Offline daily equity candidate plugins. No broker, credentials or model calls.

Independently implemented rules from the pinned sources in source-candidates.json.
These are next-open Atlas research adaptations, not reproductions of author PnL.
Each case has its own shared wallet. No parameter search or auto-promotion.
"""
import argparse
import csv
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path

CANDIDATES=('RSI2_SPY_REGIME','CONNORS_RSI2')


def load_csv(path):
    path=Path(path)
    if path.stat().st_size>64*1024**2:raise ValueError('DAILY_INPUT_BOUND')
    rows={};last={}
    with path.open(encoding='utf-8-sig',newline='') as f:
        for count,row in enumerate(csv.DictReader(f),1):
            if count>300000:raise ValueError('DAILY_ROW_BOUND')
            symbol=row['symbol'];day=date.fromisoformat(row['date']).isoformat()
            if not symbol or len(symbol)>16 or (symbol in last and day<=last[symbol]):
                raise ValueError('SORTED_UNIQUE_SYMBOL_DATES_REQUIRED')
            values={k:float(row[k]) for k in ('open','high','low','close','volume')}
            if not all(math.isfinite(v) for v in values.values()) or min(values[k] for k in ('open','high','low','close'))<=0 or values['volume']<0:
                raise ValueError('FINITE_OHLCV_REQUIRED')
            if not values['low']<=min(values['open'],values['close'])<=max(values['open'],values['close'])<=values['high']:
                raise ValueError('INVALID_OHLC')
            rows.setdefault(symbol,{})[day]=values;last[symbol]=day
    if 'SPY' not in rows:raise ValueError('SPY_REGIME_DATA_REQUIRED_NO_FALLBACK')
    return rows


def features(bars,mode):
    closes=[];gain=loss=None;out={}
    for day,b in sorted(bars.items()):
        c=b['close'];delta=c-closes[-1] if closes else None;closes.append(c)
        if delta is None:rsi=50.
        else:
            g=max(delta,0);l=max(-delta,0)
            if mode=='RSI2_SPY_REGIME':
                gain=g if gain is None else .5*g+.5*gain
                loss=l if loss is None else .5*l+.5*loss
                # Preserve the author's zero-loss -> NaN -> 50 convention.
                rsi=50. if loss==0 else 100.-100./(1.+gain/loss)
            elif len(closes)==2:
                gain=g;loss=l;rsi=50.
            else:
                gain=(gain+g)/2;loss=(loss+l)/2
                rsi=(100. if loss==0 else 0. if gain==0 else 100.-100./(1.+gain/loss))
        out[day]={'rsi':rsi,'sma200':sum(closes[-200:])/200 if len(closes)>=200 else None,
                  'sma5':sum(closes[-5:])/5 if len(closes)>=5 else None,'close':c}
    return out


def signals(rows,mode):
    if mode not in CANDIDATES:raise ValueError('REGISTERED_CANDIDATE_REQUIRED')
    f={s:features(b,mode) for s,b in rows.items()};out={}
    for symbol,bars in rows.items():
        if symbol=='SPY' and mode=='RSI2_SPY_REGIME':continue
        for day,b in bars.items():
            x=f[symbol][day];spy=f['SPY'].get(day)
            if x['sma200'] is None:continue
            regime=bool(spy and spy['sma200'] is not None and spy['close']>spy['sma200'])
            buy=x['close']>x['sma200'] and x['rsi']<(5 if mode=='RSI2_SPY_REGIME' else 10)
            if mode=='RSI2_SPY_REGIME':buy=buy and regime
            sell=x['rsi']>60 if mode=='RSI2_SPY_REGIME' else x['close']>x['sma5']
            out[(day,symbol)]=(buy,sell,x['close'])
    return out


def replay(rows,mode,start,*,wallet=10000.,stake=1000.,fee_bps=5.,slippage_bps=5.,overhead=0.,include_curve=False):
    for v in (wallet,stake,fee_bps,slippage_bps,overhead):
        if not math.isfinite(v) or v<0:raise ValueError('FINITE_NONNEGATIVE_COSTS_REQUIRED')
    if not wallet or not stake or slippage_bps>=10000:raise ValueError('BOUNDED_CAPITAL_AND_SLIPPAGE_REQUIRED')
    start=date.fromisoformat(start).isoformat();events=signals(rows,mode)
    calendar=sorted(rows['SPY']);cash=wallet;positions={};pending={};trades=[];skipped=0;curve=[];marks={}
    fee=fee_bps/10000;slip=slippage_bps/10000
    for day in calendar:
        # Signals from a completed prior session execute only on the next SPY
        # session. A missing symbol bar is an exclusion, never a fabricated fill.
        for symbol,action in sorted(pending.items(),key=lambda x:(x[1][0]!='SELL',x[0])):
            if day not in rows[symbol]:skipped+=1;continue
            side,decision_at=action
            price=rows[symbol][day]['open']*(1+slip if side=='BUY' else 1-slip)
            if side=='SELL' and symbol in positions:
                p=positions.pop(symbol);proceeds=p['quantity']*price*(1-fee);cash+=proceeds
                trades.append(dict(symbol=symbol,signal_at=p['signal_at'],entry_at=p['entry_at'],exit_signal_at=decision_at,
                    exit_at=day,entry_price=p['entry_price'],exit_price=price,quantity=p['quantity'],net_pnl=proceeds-p['cost']))
            elif side=='BUY' and symbol not in positions:
                quantity=int(min(stake,cash)/(price*(1+fee)))
                if quantity:
                    cost=quantity*price*(1+fee);cash-=cost
                    positions[symbol]=dict(quantity=quantity,cost=cost,entry_price=price,entry_at=day,signal_at=decision_at)
        pending={}
        if day>=start:
            for symbol in sorted(rows):
                e=events.get((day,symbol))
                if e is None:continue
                buy,sell,close=e
                if symbol in positions:
                    # Upstream regime strategy's 10% backstop is close-based.
                    if sell or (mode=='RSI2_SPY_REGIME' and close<=positions[symbol]['entry_price']*.9):pending[symbol]=('SELL',day)
                elif buy:pending[symbol]=('BUY',day)
        for symbol,bars in rows.items():
            if day in bars:marks[symbol]=bars[day]['close']
        equity=cash+sum(p['quantity']*marks[symbol] for symbol,p in positions.items())
        curve.append(equity)
    profits=[t['net_pnl'] for t in trades];wins=sum(p>0 for p in profits);gross_loss=-sum(p for p in profits if p<0)
    peak=wallet;dd=0
    for e in curve:peak=max(peak,e);dd=max(dd,(peak-e)/peak)
    result=dict(candidate=mode,status='OFFLINE_ADAPTATION_NOT_PROMOTED',closed_trades=len(trades),wins=wins,
        losses=sum(p<0 for p in profits),breakeven=sum(p==0 for p in profits),
        win_rate=wins/len(profits) if profits else None,realized_net=sum(profits),
        realized_after_fixed_overhead=sum(profits)-overhead,
        profit_factor=sum(p for p in profits if p>0)/gross_loss if gross_loss else None,
        ending_cash=cash,ending_marked_equity=curve[-1] if curve else wallet,
        marked_equity_after_fixed_overhead=(curve[-1] if curve else wallet)-overhead,
        maximum_drawdown=dd,open_positions=positions,unexecuted_final_signals=pending,
        missing_next_session_bars=skipped,trades=trades,
        costs=dict(commission_bps_per_side=fee_bps,adverse_slippage_bps_per_side=slippage_bps,fixed_overhead=overhead),
        source_faithful=False,orders=0,execution_authorized=False,
        limitations=['Daily swing strategies, not cent scalps; adjusted OHLCV must be consistent across symbols',
                    'Next-open fills and shared cash differ from author engines; no author result reproduced',
                    'Connors original has no stop; research only, not attached to native execution',
                    'Open positions marked separately, not counted as closed wins; universe selection bias is not repaired'])

    if include_curve:
        result['curve']=[dict(at=day,equity=value) for day,value in zip(calendar,curve) if day>=start]
        result['events']=[dict(at=t['entry_at'],action='BUY') for t in trades]+[dict(at=p['entry_at'],action='BUY') for p in positions.values()]
        result['entries']=len(result['events'])
        result['marked_net']=result['ending_marked_equity']-wallet
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--daily-csv',type=Path,required=True)
    p.add_argument('--evaluation-start',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fixed-overhead',type=float,default=0)
    a=p.parse_args();rows=load_csv(a.daily_csv)
    report=dict(scope='PINNED_SOURCE_CANDIDATE_DAILY_LAB_V1',input_sha256=sha256(a.daily_csv.read_bytes()).hexdigest(),
        evaluation_start=a.evaluation_start,cases=[replay(rows,c,a.evaluation_start,overhead=a.fixed_overhead) for c in CANDIDATES],promotion='NONE')
    with a.output.open('x') as f:json.dump(report,f,indent=2,allow_nan=False)
    for c in report['cases']:print(f"CANDIDATE={c['candidate']}; CLOSED={c['closed_trades']}; WIN_RATE={c['win_rate']}; NET={c['realized_net']}")


if __name__=='__main__':main()

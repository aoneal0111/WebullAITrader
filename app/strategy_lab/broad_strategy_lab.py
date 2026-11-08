"""Independent fixed-rule research adaptations; no provider or order imports."""
from dataclasses import dataclass
import json,math
from pathlib import Path
from hashlib import sha256

@dataclass(frozen=True)
class Case:
    family:str
    parameter:object
    @property
    def identity(self):return self.family+':'+json.dumps(self.parameter,separators=(',',':'))


def indicators(frame):
    import pandas as pd
    import numpy as np
    f=frame.copy().sort_values('date').reset_index(drop=True)
    f['date']=pd.to_datetime(f['date'],utc=True)
    if f['date'].duplicated().any():raise ValueError('DUPLICATE_BARS')
    for k in ('open','high','low','close','volume'):
        if not np.isfinite(f[k]).all() or (f[k]<0).any():raise ValueError('FINITE_POSITIVE_OHLCV_REQUIRED')
    if ((f['low']>f[['open','close']].min(axis=1))|(f['high']<f[['open','close']].max(axis=1))|(f['low']<=0)).any():raise ValueError('OHLC_SHAPE')
    c=f['close'];delta=c.diff()
    for n in (2,14):
        gain=delta.clip(lower=0).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
        loss=(-delta.clip(upper=0)).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
        f['rsi'+str(n)]=100-100/(1+gain/loss)
        f.loc[(loss==0)&(gain>0),'rsi'+str(n)]=100
        f.loc[(loss==0)&(gain==0),'rsi'+str(n)]=50
    for n in (5,10,20,50,100,200):
        f['sma'+str(n)]=c.rolling(n).mean();f['ema'+str(n)]=c.ewm(span=n,adjust=False,min_periods=n).mean()
    f['std20']=c.rolling(20).std(ddof=0)
    f['ibs']=((c-f['low'])/(f['high']-f['low'])).where(f['high']!=f['low'],.5)
    tr=pd.concat([f['high']-f['low'],(f['high']-c.shift()).abs(),(f['low']-c.shift()).abs()],axis=1).max(axis=1)
    f['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    f['roc3']=c.pct_change(3,fill_method=None);f['roc20']=c.pct_change(20,fill_method=None)
    f['pctb']=(c-(f['sma20']-2*f['std20']))/(4*f['std20'])
    f['wr2']=-100*(f['high'].rolling(2).max()-c)/(f['high'].rolling(2).max()-f['low'].rolling(2).min())
    for n in (5,10,20,50,55,100):
        f['priorhigh'+str(n)]=f['high'].rolling(n).max().shift()
        f['priorlow'+str(n)]=f['low'].rolling(n).min().shift()
    width=4*f['std20']/f['sma20'];f['prior_squeeze']=width.shift()<width.rolling(126).quantile(.2).shift()
    f['volmean']=f['volume'].rolling(20).mean()
    return f


def rules(frame,case):
    f=frame;c=f['close'];p=case.parameter;n=case.family;trend=c>f['sma200'];timeout=10;stop=3.;target=None;trail=None
    if n=='RSI2_TREND':buy=trend&(f['rsi2']<p);sell=c>f['sma5']
    elif n=='RSI2_FAST':buy=(c>f['sma50'])&(f['rsi2']<p);sell=f['rsi2']>70;timeout=5
    elif n=='IBS_TREND':buy=trend&(f['ibs']<p);sell=(f['ibs']>.8)|(c>f['sma5']);timeout=5
    elif n=='IBS_RANGE':buy=f['ibs']<p;sell=(f['ibs']>.8)|(c>f['sma5']);timeout=5
    elif n=='BB_REVERSION':buy=c<f['sma20']-p*f['std20'];sell=c>=f['sma20']
    elif n=='ROC3_PULLBACK':buy=trend&(f['roc3']<-p);sell=c>f['sma5']
    elif n=='WILLIAMS_R':buy=(c>f['sma100'])&(f['wr2']<p);sell=f['wr2']>-20;timeout=8
    elif n=='PCTB_RSI':buy=trend&(f['pctb']<p)&(f['rsi2']<20);sell=f['pctb']>.8;timeout=8
    elif n=='DONCHIAN':
        buy=c>f['priorhigh'+str(p)];sell=c<f['priorlow'+str({20:10,55:20,100:50}[p])];stop=2.;trail=3.;timeout=120
    elif n=='TREND_PULLBACK':
        ema=f['ema'+str(p)];buy=trend&(f['low']<=ema)&(c>ema)&(c>=f['open']);sell=c<f['ema50'];stop=2.;target=3.;timeout=15
    elif n=='ATR_BREAKOUT':buy=(c>f['sma50'])&(c>f['priorhigh20']+p*f['atr']);sell=c<f['ema20'];stop=2.;trail=3.;timeout=30
    elif n=='SQUEEZE_BREAKOUT':buy=f['prior_squeeze']&(c>f['priorhigh'+str(p)])&(f['volume']>f['volmean']);sell=c<f['ema20'];stop=2.;target=3.;timeout=20
    elif n=='ROC_MOMENTUM':buy=(f['roc20']>p)&(c>f['ema50']);sell=f['roc20']<0;trail=3.;timeout=30
    elif n=='EMA_CROSS':
        fast=f['ema'+str(p[0])];slow=f['ema'+str(p[1])];buy=(fast>slow)&(fast.shift()<=slow.shift());sell=fast<slow;trail=3.;timeout=120
    elif n=='TURNAROUND_TUESDAY':
        buy=(f['date'].dt.weekday==0)&(c<f['sma5'])&(c.pct_change(fill_method=None)<-p);sell=c.isna();timeout=1
    else:raise ValueError('UNKNOWN_FIXED_FAMILY')
    return dict(buy=buy.fillna(False)&(f['volume']>0)&f.get('eligible',True),sell=sell.fillna(False),timeout=timeout,stop_atr=stop,target_atr=target,trail_atr=trail)


def prepare_replay(frames,case,bars_by_date=None):
    bars_by_date=bars_by_date if bars_by_date is not None else {}
    if not bars_by_date:
        for symbol,frame in frames.items():
            for row in frame[['date','open','high','low','close','volume']].itertuples(index=False):
                bars_by_date.setdefault(row.date,{})[symbol]=dict(open=row.open,high=row.high,low=row.low,close=row.close,volume=row.volume)
    allrules={s:rules(f,case) for s,f in frames.items()}
    signals={s:{at:(bool(buy),bool(sell),float(atr)) for at,buy,sell,atr in zip(f['date'],allrules[s]['buy'],allrules[s]['sell'],f['atr'])} for s,f in frames.items()}
    return bars_by_date,allrules,signals


def replay(frames,case,calendar,*,fee,slip,initial=10000.,slots=6,equity_shares=False,prepared=None,min_notional=0.,signal_exit_floor=None):
    if signal_exit_floor is not None and (not math.isfinite(signal_exit_floor) or signal_exit_floor<0):raise ValueError('NONNEGATIVE_SIGNAL_EXIT_FLOOR_REQUIRED')
    if not math.isfinite(min_notional) or min_notional<0:raise ValueError('MINIMUM_NOTIONAL_REQUIRED')
    if not calendar or not 0<=fee<.1 or not 0<=slip<.1 or initial<=0 or slots<=0:raise ValueError('BOUNDED_REPLAY_INPUT_REQUIRED')
    bars_by_date,allrules,signals=prepared or prepare_replay(frames,case)
    cash=initial;positions={};pending={};marks={};trades=[];curve=[];events=[];entries=0;missing=0;capacity=0;minimum_size=0
    def close(symbol,price,at,reason):
        nonlocal cash
        p=positions.pop(symbol);exit_price=price*(1-slip);proceeds=p['qty']*exit_price*(1-fee);cash+=proceeds
        trades.append(dict(symbol=symbol,signal_at=str(p['signal']),entry_at=str(p['at']),exit_at=str(at),entry_price=p['price'],exit_price=exit_price,quantity=p['qty'],net_pnl=proceeds-p['cost'],reason=reason))
        events.append(dict(at=str(at),symbol=symbol,action='SELL',price=exit_price))
    for at in calendar:
        bars=bars_by_date.get(at,{})
        for s,order in sorted(pending.items(),key=lambda x:(x[1]['action']!='SELL',x[0])):
            if s not in bars:missing+=1;continue
            b=bars[s]
            if order['action']=='SELL':
                if s in positions:close(s,float(b['open']),at,'NEXT_OPEN_SIGNAL')
                continue
            if s in positions:continue
            if len(positions)>=slots:capacity+=1;continue
            equity=cash+sum(p['qty']*marks.get(sym,p['price']) for sym,p in positions.items())
            px=float(b['open'])*(1+slip);budget=min(cash,equity/slots)
            qty=budget/(px*(1+fee));qty=math.floor(qty) if equity_shares else math.floor(qty*1e8)/1e8
            if qty<=0:continue
            if qty*px<min_notional:minimum_size+=1;continue
            cost=qty*px*(1+fee);atr=order['atr'];cfg=allrules[s]
            if not math.isfinite(atr) or atr<=0:continue
            cash-=cost;positions[s]=dict(qty=qty,cost=cost,price=px,at=at,signal=order['signal'],bars=0,stop=max(.01*px,px-cfg['stop_atr']*atr),target=px+cfg['target_atr']*atr if cfg['target_atr'] else None)
            entries+=1;events.append(dict(at=str(at),signal_at=str(order['signal']),symbol=s,action='BUY',price=px))
        pending={}
        for s in list(positions):
            if s not in bars:continue
            b=bars[s];p=positions[s];p['bars']+=1;op=float(b['open']);lo=float(b['low']);hi=float(b['high'])
            if op<=p['stop']:close(s,op,at,'GAP_STOP')
            elif lo<=p['stop']:close(s,p['stop'],at,'INTRABAR_STOP')
            elif p['target'] is not None and hi>=p['target']:close(s,p['target'],at,'INTRABAR_TARGET')
        for s,b in bars.items():marks[s]=float(b['close'])
        for s,b in bars.items():
            buy,sell,atr=signals[s][at];cfg=allrules[s]
            if s in positions:
                p=positions[s]
                estimated_net_return=p['qty']*float(b['close'])*(1-slip)*(1-fee)/p['cost']-1
                signal_exit=bool(sell) and (signal_exit_floor is None or estimated_net_return>=signal_exit_floor)
                if signal_exit or p['bars']>=cfg['timeout']:pending[s]=dict(action='SELL',signal=at)
                if cfg['trail_atr'] and math.isfinite(atr):p['stop']=max(p['stop'],float(b['close'])-cfg['trail_atr']*atr)
            elif buy:pending[s]=dict(action='BUY',signal=at,atr=atr)
        value=cash+sum(p['qty']*marks.get(s,p['price']) for s,p in positions.items());curve.append(dict(at=str(at),equity=value))
        if cash<-.00001:raise AssertionError('NEGATIVE_CASH_WITHOUT_LEVERAGE')
    pnl=[t['net_pnl'] for t in trades];gain=sum(p for p in pnl if p>0);loss=-sum(p for p in pnl if p<0);peak=initial;dd=0
    for item in curve:peak=max(peak,item['equity']);dd=max(dd,(peak-item['equity'])/peak)
    open_mark=sum(p['qty']*marks.get(s,p['price']) for s,p in positions.items());net=curve[-1]['equity']-initial
    # Cash + open cost + closed realized profit reconcile even with open losses.
    if abs(cash+sum(p['cost'] for p in positions.values())-initial-sum(pnl))>.00001:raise AssertionError('LEDGER_RECONCILIATION')
    days=max((calendar[-1]-calendar[0]).total_seconds()/86400,1)
    return dict(case=case.identity,closed_trades=len(trades),wins=sum(p>0 for p in pnl),losses=sum(p<0 for p in pnl),win_rate=sum(p>0 for p in pnl)/len(pnl) if pnl else None,
        realized_net=sum(pnl),marked_net=net,profit_factor=gain/loss if loss else (None if not gain else 999.),maximum_drawdown=dd,
        ending_cash=cash,ending_marked_equity=cash+open_mark,open_positions=len(positions),entries=entries,entries_per_month=entries*30.4375/days,
        missing_next_bar_exclusions=missing,slot_exclusions=capacity,minimum_notional_exclusions=minimum_size,costs=dict(fee_per_side=fee,slippage_per_side=slip),trades=trades,curve=curve,events=events,
        source_faithful=False,execution_authorized=False,orders=0)


def compact(result):return {k:v for k,v in result.items() if k not in ('trades','curve','events')}
def passes(row,gate):
    return (row['closed_trades']>=gate['minimum_closed_trades'] and row['win_rate'] is not None
        and row['win_rate']>=gate['minimum_win_rate'] and row['profit_factor'] is not None and row['profit_factor']>=gate['minimum_profit_factor']
        and row['marked_net']>0 and row['maximum_drawdown']<=gate['maximum_drawdown'])


def compare(root):
    import pandas as pd
    root=Path(root);protocol=json.loads((root/'protocol.json').read_text());cases=[Case(f,p) for f,values in protocol['families'].items() for p in values]
    report=dict(scope=protocol['scope'],protocol_sha256=sha256((root/'protocol.json').read_bytes()).hexdigest(),comparisons={},promotion='NONE',orders=0)
    for market,settings in protocol['markets'].items():
        for tf in settings['timeframes']:
            key=market+'-'+tf;frames={}
            for symbol in settings['symbols']:
                path=root/'data'/(market+'-'+symbol+'-'+tf+'.json')
                if path.exists():
                    frames[symbol]=indicators(pd.DataFrame(json.loads(path.read_text())))
                    frames[symbol]['eligible']=frames[symbol].index>=protocol['warmup']
            benchmark='BTC' if market=='crypto' else 'SPY'
            if benchmark not in frames:raise ValueError('BENCHMARK_MISSING')
            dates=list(frames[benchmark]['date'].iloc[protocol['warmup']:]);a=len(dates)//2;b=a+(len(dates)-a)//2
            segments={'development':dates[:a],'validation':dates[a:b],'holdout':dates[b:]}
            rows=[];cache={};baseline=.008 if market=='crypto' else .0005;bar_cache={}
            for case in cases:
                if case.family=='TURNAROUND_TUESDAY' and (market!='equity' or tf!='1d'):continue
                entry=dict(case=case.identity,development={},validation={})
                prepared=prepare_replay(frames,case,bar_cache);bar_cache=prepared[0]
                for segment in ('development','validation'):
                    for fee in settings['fee_per_side']:
                        r=replay(frames,case,segments[segment],fee=fee,slip=settings['slippage_per_side'],initial=protocol['initial_cash'],slots=protocol['simultaneous_slots'],equity_shares=market=='equity',prepared=prepared)
                        entry[segment][str(fee)]=compact(r)
                entry['development_pass']=passes(entry['development'][str(baseline)],protocol['development_gate'])
                entry['validation_pass']=passes(entry['validation'][str(baseline)],protocol['validation_gate'])
                rows.append(entry);cache[case.identity]=case
            candidates=[r for r in rows if r['development_pass'] and r['validation_pass']]
            candidates.sort(key=lambda r:(-r['validation'][str(baseline)]['marked_net'],r['case']))
            selected=candidates[0]['case'] if candidates else None
            selection=dict(scope='FROZEN_PRE_HOLDOUT_SELECTION_V1',market_timeframe=key,selected=selected,passing_development_validation=len(candidates),baseline_fee=baseline,
                segments={name:dict(start=str(v[0]),end=str(v[-1]),bars=len(v)) for name,v in segments.items()})
            selection_path=root/('selection-'+key+'.json')
            with selection_path.open('x') as f:json.dump(selection,f,indent=2)
            holdouts={}
            if selected:
                for fee in settings['fee_per_side']:
                    r=replay(frames,cache[selected],segments['holdout'],fee=fee,slip=settings['slippage_per_side'],initial=protocol['initial_cash'],slots=protocol['simultaneous_slots'],equity_shares=market=='equity')
                    r['passed_holdout_gate']=passes(r,protocol['holdout_gate']);holdouts[str(fee)]=compact(r)
                    (root/('selected-ledger-'+key+'-'+str(fee)+'.json')).write_text(json.dumps(r,indent=2))
            report['comparisons'][key]=dict(symbols=list(frames),selection=selection,cases=rows,holdout=holdouts)
            (root/'comparison.json').write_text(json.dumps(report,indent=2,allow_nan=False))
            print('COMPARISON',key,'CASES',len(rows),'SELECTED',selected,'PASSES',len(candidates),flush=True)
    return report


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True,
                        help='Prepared research directory with protocol.json and data/*.json; use a new output directory for every study')
    args=parser.parse_args()
    compare(args.root)


if __name__=='__main__':
    main()

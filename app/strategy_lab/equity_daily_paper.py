"""Isolated daily equity PAPER proxies. Public prices only, no broker write API.

Daily source rules are evaluated on completed candles. Orders fill at the first
fresh observed regular-session public price, not a retroactive historical open.
This is a separate execution model from the next-open research backtest.
"""
import argparse
from datetime import UTC,datetime,time as clock,timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from hashlib import sha256
import json,math,sqlite3,time
from urllib.request import Request,urlopen
from app.strategy_research.source_candidate_lab import signals,CANDIDATES,features

SYMBOLS=('SPY','QQQ','IWM','DIA','XLK','XLF','XLE','XLI','XLP','XLU','GLD','TLT')
NY=ZoneInfo('America/New_York')


def write(path,value):
    tmp=Path(str(path)+'.tmp');tmp.write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8');tmp.replace(path)


def fetch(symbol):
    if symbol not in SYMBOLS:raise ValueError('RESEARCH_UNIVERSE_REQUIRED')
    url=f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5y&interval=1d'
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0 AtlasPaperResearch'}),timeout=20) as r:
        raw=r.read(8*1024*1024+1)
    if len(raw)>8*1024*1024:raise ValueError('PUBLIC_RESPONSE_TOO_LARGE')
    return raw


def parse(raw,now):
    result=json.loads(raw)['chart']['result'][0];meta=result['meta'];q=result['indicators']['quote'][0]
    adj=result['indicators']['adjclose'][0]['adjclose'];bars={}
    for i,t in enumerate(result.get('timestamp',[])):
        at=datetime.fromtimestamp(t,UTC).astimezone(NY)
        if now.astimezone(NY).date()<=at.date():continue  # today's forming bar is never an input
        vals=[q[k][i] for k in ('open','high','low','close','volume')]
        if any(v is None or not math.isfinite(v) for v in vals) or not adj[i] or vals[3]<=0:continue
        scale=adj[i]/vals[3]
        b={k:v*(1 if k=='volume' else scale) for k,v in zip(('open','high','low','close','volume'),vals)}
        if b['low']<=0 or b['low']>min(b['open'],b['close']) or b['high']<max(b['open'],b['close']):raise ValueError('INVALID_PUBLIC_OHLC')
        bars[at.date().isoformat()]=b
    price=meta.get('regularMarketPrice');stamp=meta.get('regularMarketTime')
    if price is None or stamp is None or not math.isfinite(price) or price<=0:raise ValueError('PUBLIC_PRICE_REQUIRED')
    local=now.astimezone(NY);at=datetime.fromtimestamp(stamp,UTC)
    period=meta.get('currentTradingPeriod',{}).get('regular',{})
    market_open=bool(local.weekday()<5 and clock(9,30)<=local.time().replace(tzinfo=None)<clock(16)
        and period.get('start',float('inf'))<=now.timestamp()<period.get('end',0))
    active=market_open and at.astimezone(NY).date()==local.date() and -2<=(now-at).total_seconds()<=120
    return bars,dict(price=price,at=at.isoformat(),fresh_regular_session=active,market_open=market_open,
                    observed_age_seconds=(now-at).total_seconds(),source='YAHOO_PUBLIC_TRADE_PRICE_PROXY_NOT_EXECUTABLE_NBBO')


def database(path):
    db=sqlite3.connect(path,timeout=10)
    db.executescript('''PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS wallets(strategy TEXT PRIMARY KEY,cash REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS positions(strategy TEXT,symbol TEXT,quantity INTEGER,entry REAL,cost REAL,entry_signal TEXT,PRIMARY KEY(strategy,symbol));
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,strategy TEXT,symbol TEXT,side TEXT,signal_day TEXT,at TEXT,quantity INTEGER,price REAL,fee REAL,net REAL,model TEXT,UNIQUE(strategy,symbol,side,signal_day));
    CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,at TEXT,payload TEXT);
    ''')
    for case in CANDIDATES:db.execute('INSERT OR IGNORE INTO wallets VALUES(?,10000)',(case,))
    db.commit();return db


def cycle(db,rows,quotes,now,*,paused=False):
    reasons={};events=[]
    for case in CANDIDATES:
        decisions=signals(rows,case) if 'SPY' in rows else {}
        # Exits first; each strategy has independent cash and holdings.
        for phase in ('SELL','BUY'):
            for symbol in SYMBOLS:
                quote=quotes.get(symbol);bars=rows.get(symbol,{})
                if not quote or len(bars)<200:reasons[case+'|'+symbol]='WARMUP_OR_DATA_MISSING';continue
                day=max(bars);spy_days=rows.get('SPY',{})
                buy,sell,_=decisions.get((day,symbol),(False,False,0))
                pos=db.execute('SELECT quantity,entry,cost,entry_signal FROM positions WHERE strategy=? AND symbol=?',(case,symbol)).fetchone()
                if pos:
                    x=features(bars,case)[day]
                    sell=(x['rsi']>60 if case=='RSI2_SPY_REGIME' else x['sma5'] is not None and x['close']>x['sma5'])
                if not pos and (not spy_days or max(spy_days)!=day):reasons[case+'|'+symbol]='BENCHMARK_DATE_MISMATCH';continue
                reason='HOLDING' if pos else 'NO_BUY_SIGNAL'
                stop=bool(pos and case=='RSI2_SPY_REGIME' and bars[day]['close']<=pos[1]*.9)
                action='SELL' if pos and (sell or stop) else 'BUY' if buy and not pos else None
                observed=datetime.fromisoformat(quote['at'])
                fresh=(quote['fresh_regular_session'] and -2<=(now-observed).total_seconds()<=120
                    and datetime.fromisoformat(day).date()>=now.date()-timedelta(days=7))
                local=now.astimezone(NY)
                market_open=quote.get('market_open',local.weekday()<5 and clock(9,30)<=local.time().replace(tzinfo=None)<clock(16))
                if action:
                    reason='WAIT_MARKET_OPEN' if not market_open else 'STALE_PUBLIC_PRICE_OR_DAILY_HISTORY' if not fresh else 'OPERATOR_PAUSE' if paused and action=='BUY' else 'READY_'+action
                reasons[case+'|'+symbol]=reason
                if action!=phase or reason!='READY_'+action:continue
                if action=='BUY' and db.execute('SELECT 1 FROM orders WHERE strategy=? AND symbol=? AND side="SELL" AND signal_day=?',(case,symbol,day)).fetchone():
                    reasons[case+'|'+symbol]='EXIT_ALREADY_PROCESSED_TODAY';continue
                if db.execute('SELECT 1 FROM orders WHERE strategy=? AND symbol=? AND side=? AND signal_day=?',(case,symbol,action,day)).fetchone():
                    reasons[case+'|'+symbol]='SIGNAL_ALREADY_PROCESSED';continue
                cash=db.execute('SELECT cash FROM wallets WHERE strategy=?',(case,)).fetchone()[0]
                fee=.0005;slip=.0005;px=quote['price']*(1+slip if action=='BUY' else 1-slip);net=None
                if action=='BUY':
                    qty=math.floor(min(1000,cash)/(px*(1+fee)))
                    if qty<=0:reasons[case+'|'+symbol]='INSUFFICIENT_SIMULATED_CASH';continue
                    cost=qty*px*(1+fee);cash-=cost
                    db.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',(case,symbol,qty,px,cost,day))
                else:
                    qty=pos[0];proceeds=qty*px*(1-fee);cash+=proceeds;net=proceeds-pos[2]
                    db.execute('DELETE FROM positions WHERE strategy=? AND symbol=?',(case,symbol))
                db.execute('UPDATE wallets SET cash=? WHERE strategy=?',(cash,case))
                event=dict(strategy=case,symbol=symbol,side=action,signal_day=day,at=now.isoformat(),quantity=qty,price=px,fee=qty*px*fee,net=net,model='FRESH_PUBLIC_PRICE_PAPER_PROXY')
                db.execute('INSERT INTO orders(strategy,symbol,side,signal_day,at,quantity,price,fee,net,model) VALUES(:strategy,:symbol,:side,:signal_day,:at,:quantity,:price,:fee,:net,:model)',event)
                events.append(event);reasons[case+'|'+symbol]='PAPER_'+action+'_FILLED'
    # Persist evidence and transactions together; rollback prevents an unrecorded buy.
    payload=dict(at=now.isoformat(),reasons=reasons,orders=events,quotes=quotes,
        completed_input_sha256={s:sha256(json.dumps(b,sort_keys=True).encode()).hexdigest() for s,b in rows.items()})
    db.execute('INSERT INTO observations(at,payload) VALUES(?,?)',(now.isoformat(),json.dumps(payload)))
    db.commit();return payload


def status(db):
    out={}
    for case in CANDIDATES:
        trades=db.execute('SELECT net FROM orders WHERE strategy=? AND side="SELL"',(case,)).fetchall()
        wins=sum(x[0]>0 for x in trades)
        out[case]=dict(cash=db.execute('SELECT cash FROM wallets WHERE strategy=?',(case,)).fetchone()[0],
            open_positions=db.execute('SELECT symbol,quantity,entry FROM positions WHERE strategy=?',(case,)).fetchall(),
            simulated_orders=db.execute('SELECT count(*) FROM orders WHERE strategy=?',(case,)).fetchone()[0],
            closed_trades=len(trades),wins=wins,closed_win_rate=wins/len(trades) if trades else None,
            realized_net=sum(x[0] for x in trades))
    return out


def run(work):
    import fcntl
    work=Path(work)
    with (work/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db=database(work/'equity-paper.sqlite3')
        while not (work/'STOP').exists():
            now=datetime.now(UTC);rows={};quotes={};errors={}
            for symbol in SYMBOLS:
                try:
                    raw=fetch(symbol);folder=work/'public-data'/symbol;folder.mkdir(parents=True,exist_ok=True)
                    rows[symbol],quotes[symbol]=parse(raw,datetime.now(UTC))
                    # Store one raw snapshot per distinct completed input. Forming
                    # daily changes are ignored by rules; every observed price is in SQLite.
                    name=sha256(json.dumps(rows[symbol],sort_keys=True).encode()).hexdigest()+'.json';path=folder/name
                    if not path.exists():path.write_bytes(raw)
                except Exception as exc:errors[symbol]=type(exc).__name__+':'+str(exc)[:180]
                time.sleep(1)
            try:
                result=cycle(db,rows,quotes,datetime.now(UTC),paused=(work/'PAUSE_NEW_ENTRIES').exists())
                write(work/'engine-status.json',dict(state='RUNNING',updated_at=datetime.now(UTC).isoformat(),
                    live_trading_enabled=False,broker_orders=0,price_model='PUBLIC_PRICE_PROXY',errors=errors,
                    last_cycle=result,strategies=status(db),capture_ok=True,market='US_EQUITY_REGULAR_SESSION_ONLY'))
            except Exception as exc:
                db.rollback();write(work/'engine-status.json',dict(state='FAILED',updated_at=datetime.now(UTC).isoformat(),error=type(exc).__name__));raise
            for _ in range(300):
                if (work/'STOP').exists():break
                time.sleep(1)
        write(work/'engine-status.json',dict(state='STOPPED',updated_at=datetime.now(UTC).isoformat(),strategies=status(db),live_trading_enabled=False))
        db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True);a=p.parse_args();run(a.work)


if __name__=='__main__':main()

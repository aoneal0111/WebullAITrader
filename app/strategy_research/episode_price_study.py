"""Frozen, sampled, chronological daily-wallet OHLC study. No order authority."""
import argparse
from collections import Counter,OrderedDict
from dataclasses import asdict,dataclass
from datetime import UTC,datetime,timedelta,time as clocktime
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

from .equity_corpus_review import REPORT,read_json,review_report,MAX_REPORT
from .episode_exit_mechanics import ExitPolicy,next_minute_entry,replay_long,size_entry

NY=ZoneInfo('America/New_York')
SOURCE_SHA='faeb26493d4c836c36d6daa4665925ec0cef3607af024676f13bc43d688ac73a'
FAMILIES=('HOD_RECLAIM','BREAKOUT_RETEST_CONTINUATION','HIGH_OF_DAY_BREAKOUT','OPENING_RANGE_BREAKOUT','FIRST_PULLBACK')
POLICIES=(ExitPolicy('FULL_050R',target_r=D('.5'),partial_fraction=D(1)),
          ExitPolicy('FULL_1R',partial_fraction=D(1)),
          ExitPolicy('HALF_050R_BE',target_r=D('.5'),partial_fraction=D('.5'),break_even_after_partial=True),
          ExitPolicy('HALF_1R_BE',partial_fraction=D('.5'),break_even_after_partial=True))
COSTS=(D('.01'),D('.02'))
VERSION='EPISODE_NEXT_OPEN_DAILY_WALLET_V1'


def plan():
    return dict(version=VERSION,source_sha256=SOURCE_SHA,families=FAMILIES,
        policies=[asdict(p) for p in POLICIES],cost_per_share_per_side=COSTS,
        selection='Lowest SHA256(version|symbol|date), up to 200 eligible symbol-days per month; all their family episodes',
        dates='2026-06-01 through 2026-08-31; already inspected historical period',
        entry='Exact minute after detection minute; source history bars must be completed at detection',
        prefix_adapter='COMPLETED_EPISODE_TIMES_V2_NORMALIZED_OHLCV_DIGEST_MATCH',
        wallet_per_case_per_day='1000',max_positions=2,max_notional='500',risk_with_cost='10',
        max_hold_minutes=60,volume_participation='1% of preceding completed minute volume',
        capital='Full entry cash reserved until complete exit; partial proceeds not reinvested',
        priority='Exits known by entry time first, then symbol and episode ID at equal entry times',
        unknowns='Ambiguity/missing bars keep reservation through day end; never fabricated liquidation',
        headline='Only fully resolved independent days; excluded days and conditional closed trades reported separately',
        promotion='NONE',orders=0,execution_authorized=False)


@dataclass(frozen=True)
class PriceBar:
    symbol:str
    timestamp:datetime
    open:D
    high:D
    low:D
    close:D
    volume:D


def canonical_bar(at,values):
    numbers=[D(str(v)) for v in values]
    if any(not n.is_finite() for n in numbers):raise ValueError('FINITE_PREFIX_PRICES_REQUIRED')
    return [at.astimezone(UTC).isoformat(),*[format(n.normalize(),'f') for n in numbers]]


def prefix_evidence(row,at):
    """Episode timestamps are completion times, unlike normalized open times.

    This contract is from mining._candidate, not a freshness allowance. The
    resulting digest must also match the normalized history before any entry.
    """
    bars=row.get('bar_window')
    if not isinstance(bars,list) or not bars:return dict(ok=False,status='MISSING_WINDOW')
    try:
        cutoff=datetime.fromisoformat(row['provenance']['decision_cutoff'])
        if cutoff.utcoffset() is None or cutoff!=at:return dict(ok=False,status='CUTOFF_MISMATCH')
        opens=[];values=[];previous=None
        for bar in bars:
            end=datetime.fromisoformat(bar['timestamp'])
            if (end.utcoffset() is None or end>at or end.second or end.microsecond
                    or previous is not None and end<=previous or bar['symbol']!=row['symbol']):
                return dict(ok=False,status='FUTURE_OR_UNORDERED_WINDOW')
            opened=end-timedelta(minutes=1)
            values.append(canonical_bar(opened,[bar[k] for k in ('open','high','low','close','volume')]))
            opens.append(opened);previous=end
        return dict(ok=True,status='COMPLETED_TIMESTAMPS_REQUIRE_PRICE_MATCH',count=len(values),
            first_open=opens[0].astimezone(UTC).isoformat(),last_open=opens[-1].astimezone(UTC).isoformat(),
            sha256=sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest())
    except (KeyError,TypeError,ValueError,ArithmeticError):return dict(ok=False,status='INVALID_WINDOW_SCHEMA')


def prefix_ok(row,at):
    return prefix_evidence(row,at)['ok']


def normalized_prefix_matches(proof,bars):
    if not proof or not proof.get('ok'):return False
    start=datetime.fromisoformat(proof['first_open']);end=datetime.fromisoformat(proof['last_open'])
    values=[canonical_bar(b.timestamp,(b.open,b.high,b.low,b.close,b.volume)) for b in bars if start<=b.timestamp<=end]
    return (len(values)==proof['count'] and
        sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()==proof['sha256'])


def build_index(source,db,expected_counts,expected_episodes,*,expected_sha=SOURCE_SHA,progress=print):
    before=source.stat();digest=sha256();counts=Counter();memberships=Counter();last=time.monotonic()
    if before.st_size>40_000_000_000:raise ValueError('CORPUS_BYTE_LIMIT')
    db.execute('CREATE TABLE events (id TEXT PRIMARY KEY,symbol TEXT,day TEXT,entry_time TEXT,body TEXT)')
    db.execute('CREATE TABLE partitions (symbol TEXT,day TEXT,rank TEXT,PRIMARY KEY(symbol,day))')
    with source.open('rb') as stream:
        while line:=stream.readline(1_000_001):
            if len(line)>1_000_000 or not line.endswith(b'\n'):raise ValueError('CORPUS_ROW_BOUND')
            digest.update(line);counts['rows']+=1
            if counts['rows']>2_000_000:raise ValueError('CORPUS_ROW_COUNT_BOUND')
            row=json.loads(line);labels=row.get('strategy_memberships',[])
            if not isinstance(labels,list) or any(not isinstance(s,str) for s in labels):raise ValueError('MEMBERSHIP_SCHEMA')
            memberships.update(labels)
            chosen=sorted(set(labels)&set(FAMILIES))
            if chosen:
                symbol=row['symbol'];day=row['trading_date'];at=datetime.fromisoformat(row['detected_timestamp'])
                if (not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]{0,19}',symbol) or at.utcoffset() is None
                        or at.astimezone(NY).date().isoformat()!=day or not '2026-06-01'<=day<='2026-08-31'):
                    raise ValueError('EPISODE_IDENTITY_OR_DATE')
                entry=(at.replace(second=0,microsecond=0)+timedelta(minutes=1)).astimezone(UTC)
                trigger=row.get('trigger_timestamp')
                proof=prefix_evidence(row,at);safe=proof['ok']
                counts['source_prefix_valid' if safe else 'source_prefix_invalid']+=1
                counts['prefix_'+proof['status']]+=1
                if trigger is not None:
                    trigger=datetime.fromisoformat(trigger)
                    safe=safe and trigger.utcoffset() is not None and trigger<=at
                body=dict(episode_id=row['episode_id'],symbol=symbol,day=day,detected_at=at.isoformat(),
                    planned_price=str(row['trigger_price']),stop=str(row['structural_stop']),families=chosen,prefix_ok=safe,prefix_evidence=proof)
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(row['episode_id'],symbol,day,entry.isoformat(),json.dumps(body)))
                rank=sha256(f'{VERSION}|{symbol}|{day}'.encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO partitions VALUES (?,?,?)',(symbol,day,rank))
                counts['indexed_family_episodes']+=1
                if counts['indexed_family_episodes']%5000==0:db.commit()
            if time.monotonic()-last>=10:
                progress(f'INDEX_ROWS={counts["rows"]}; BYTES={stream.tell()}/{before.st_size}',flush=True);last=time.monotonic()
    after=source.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('CORPUS_CHANGED')
    if digest.hexdigest()!=expected_sha:raise ValueError('FROZEN_CORPUS_SHA_MISMATCH')
    if counts['rows']!=expected_episodes or any(memberships[s]!=n for s,n in expected_counts.items()):
        raise ValueError('REPORT_COUNTS_MISMATCH')
    db.execute('CREATE TABLE selected (symbol TEXT,day TEXT,PRIMARY KEY(symbol,day))')
    for month in ('2026-06','2026-07','2026-08'):
        db.execute('INSERT INTO selected SELECT symbol,day FROM partitions WHERE substr(day,1,7)=? ORDER BY rank,symbol,day LIMIT 200',(month,))
    db.execute('CREATE INDEX event_clock ON events(day,entry_time,symbol,id)');db.commit()
    counts['selected_symbol_days']=db.execute('SELECT COUNT(*) FROM selected').fetchone()[0]
    return dict(counts=counts,source_sha256=digest.hexdigest())


class SourceChanged(ValueError):
    pass


class BarCache:
    def __init__(self,root):self.root=Path(root);self.cache=OrderedDict();self.proofs={}
    def get(self,symbol,day):
        key=(symbol,day)
        if key in self.cache:self.cache.move_to_end(key);return self.cache[key]
        path=self.root/f'{symbol}_{day}.jsonl';before=path.stat()
        if before.st_size>8_000_000:raise ValueError('BAR_PARTITION_BYTE_BOUND')
        with path.open('rb') as stream:raw=stream.read(8_000_001)
        if len(raw)>8_000_000:raise ValueError('BAR_PARTITION_BYTE_BOUND')
        after=path.stat();proof=(len(raw),after.st_mtime_ns,sha256(raw).hexdigest())
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise SourceChanged('BAR_FILE_CHANGED')
        if key in self.proofs and self.proofs[key]!=proof:raise SourceChanged('BAR_FILE_CHANGED')
        self.proofs[key]=proof;bars=[]
        for line in raw.splitlines():
            if len(bars)>=3000 or len(line)>100_000:raise ValueError('BAR_ROW_BOUND')
            r=json.loads(line);at=datetime.fromisoformat(r['timestamp'])
            values=[D(str(r[k])) for k in ('open','high','low','close','volume')];o,h,l,c,v=values
            if (at.utcoffset() is None or at.second or at.microsecond or at.astimezone(NY).date().isoformat()!=day
                    or r['symbol']!=symbol or any(not x.is_finite() for x in values) or min(o,h,l,c)<=0 or v<0
                    or not l<=min(o,c)<=max(o,c)<=h or bars and at<=bars[-1].timestamp):
                raise ValueError('INVALID_OR_UNORDERED_PRICE_BAR')
            bars.append(PriceBar(symbol,at,o,h,l,c,v))
        self.cache[key]=tuple(bars)
        if len(self.cache)>32:self.cache.popitem(last=False)
        return self.cache[key]
    def verify_stable(self):
        for (symbol,day),(size,mtime,_) in self.proofs.items():
            stat=(self.root/f'{symbol}_{day}.jsonl').stat()
            if (stat.st_size,stat.st_mtime_ns)!=(size,mtime):raise SourceChanged('BAR_FILE_CHANGED')


class DayWallet:
    def __init__(self,family,policy,cost,trace):
        self.family=family;self.policy=policy;self.cost=cost;self.trace=trace
        self.cash=D(1000);self.active={};self.closed=[];self.counts=Counter()
    def settle(self,at):
        due=sorted((p['known_close'],s) for s,p in self.active.items() if p['known_close'] is not None and p['known_close']<=at)
        for _,symbol in due:
            p=self.active.pop(symbol);r=p['result'];self.cash+=p['reserved']+r.net_pnl
            self.closed.append(r.net_pnl)
            self.trace(dict(kind='CLOSED',family=self.family,policy=self.policy.name,cost=self.cost,**p['audit'],net=r.net_pnl,
                fills=[asdict(f) for f in r.fills]))
    def consider(self,event,bars,at):
        self.settle(at);self.counts['signals']+=1;symbol=event['symbol']
        if symbol in self.active:self.counts['SYMBOL_ALREADY_OPEN']+=1;return
        if len(self.active)>=2:self.counts['POSITION_LIMIT']+=1;return
        if not event['prefix_ok']:self.counts['UNVERIFIED_SIGNAL_PREFIX']+=1;return
        stop=D(event['stop']);planned=D(event['planned_price'])
        if not (stop.is_finite() and planned.is_finite() and planned>stop>0):self.counts['INVALID_GEOMETRY']+=1;return
        audit=next_minute_entry(bars,symbol=symbol,detected_time=datetime.fromisoformat(event['detected_at']),planned_price=planned,initial_stop=stop)
        if audit['status']!='ENTRY_OPEN_PROXY':self.counts[audit['status']]+=1;return
        entry=audit['entry_price'];previous=next((b for b in bars if b.timestamp==at-timedelta(minutes=1)),None)
        if previous is None:self.counts['NO_COMPLETED_VOLUME_REFERENCE']+=1;return
        qty=size_entry(entry,stop,D(10),self.cost,'COST_INCLUSIVE',min(D(500),max(self.cash,D('.0001'))))['quantity']
        qty=min(qty,(previous.volume*D('.01')).to_integral_value(rounding='ROUND_FLOOR'))
        if qty<=0:self.counts['CASH_RISK_OR_VOLUME_LIMIT']+=1;return
        reserved=qty*(entry+self.cost);self.cash-=reserved
        result=replay_long(bars,symbol=symbol,entry_time=at,entry_price=entry,initial_stop=stop,
            quantity=qty,policy=self.policy,hold_minutes=60,cost_per_share_per_side=self.cost)
        close=None
        if result.status=='CLOSED':
            fill=result.fills[-1];close=fill.timestamp+(timedelta(minutes=1) if fill.timing!='BAR_OPEN' else timedelta())
        self.active[symbol]=dict(reserved=reserved,result=result,known_close=close,
            audit=dict(episode_id=event['episode_id'],symbol=symbol,entry_at=at,entry_price=entry,stop=stop,quantity=qty))
        self.counts['entries']+=1
    def finish(self,day):
        self.settle(datetime.combine(datetime.fromisoformat(day).date()+timedelta(days=1),clocktime(),NY))
        statuses=Counter(p['result'].status for p in self.active.values())
        for p in self.active.values():
            self.trace(dict(kind='UNRESOLVED',family=self.family,policy=self.policy.name,cost=self.cost,**p['audit'],
                status=p['result'].status,remaining=p['result'].remaining,reserved_cash=p['reserved'],
                fills=[asdict(f) for f in p['result'].fills]))
        return dict(day=day,counts=dict(self.counts),fully_resolved=not self.active,
            input_complete=not self.counts['INVALID_OR_MISSING_PRICE_PARTITION'],
            signal_evidence_complete=not self.counts['UNVERIFIED_SIGNAL_PREFIX'],
            closed_trades=len(self.closed),wins=sum(x>0 for x in self.closed),losses=sum(x<0 for x in self.closed),
            closed_net=sum(self.closed,D(0)),positive_net=sum((x for x in self.closed if x>0),D(0)),
            negative_net=-sum((x for x in self.closed if x<0),D(0)),ending_uncommitted_cash=self.cash,
            unresolved_positions=len(self.active),unresolved_statuses=dict(statuses),
            reserved_cash=sum((p['reserved'] for p in self.active.values()),D(0)))


def aggregate(days):
    clean=[d for d in days if d['fully_resolved'] and d['input_complete'] and d.get('signal_evidence_complete',True)];n=sum(d['closed_trades'] for d in clean);wins=sum(d['wins'] for d in clean)
    gain=sum((d['positive_net'] for d in clean),D(0));loss=sum((d['negative_net'] for d in clean),D(0))
    return dict(independent_days=len(days),fully_resolved_days=sum(d['fully_resolved'] for d in days),headline_complete_days=len(clean),excluded_unresolved_days=sum(not d['fully_resolved'] for d in days),
        excluded_price_error_days=sum(not d['input_complete'] for d in days),
        excluded_signal_evidence_days=sum(not d.get('signal_evidence_complete',True) for d in days),
        closed_trades_resolved_days=n,win_rate_resolved_days=D(wins)/n if n else None,
        net_resolved_days=gain-loss,profit_factor_resolved_days=gain/loss if loss else None,
        all_days_closed_net_conditional=sum((d['closed_net'] for d in days),D(0)),
        all_days_closed_trades=sum(d['closed_trades'] for d in days),
        unresolved_positions=sum(d['unresolved_positions'] for d in days),
        counts=dict(sum((Counter(d['counts']) for d in days),Counter())),
        unresolved_statuses=dict(sum((Counter(d['unresolved_statuses']) for d in days),Counter())))


def run(repo,output,*,progress=print):
    repo=Path(repo).resolve();output=Path(output);output.mkdir(parents=True,exist_ok=False)
    frozen=plan();frozen['source_files']={p.name:sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path(__file__).with_name('episode_exit_mechanics.py'))}
    (output/'frozen-plan.json').write_text(json.dumps(frozen,default=str,indent=2),encoding='utf-8')
    report,rhash=read_json(repo/REPORT,MAX_REPORT);review=review_report(report);root=(repo/REPORT).parent.parent
    db=sqlite3.connect(output/'private-episode-index.sqlite3');case_days={};cache=BarCache(root/'normalized');prefix_counts=Counter()
    try:
        provenance=build_index(root/'corpus_repaired/episodes.jsonl',db,
            {r['strategy']:r['whole_sample_memberships'] for r in review['strategies']},report['metadata']['records_ingested'],progress=progress)
        day_list=[r[0] for r in db.execute('SELECT DISTINCT day FROM selected ORDER BY day')]
        with (output/'private-price-replay.jsonl').open('w',encoding='utf-8') as stream:
            def trace(record):stream.write(json.dumps(record,default=str,separators=(',',':'))+'\n')
            for day in day_list:
                lanes={(f,p.name,str(c)):DayWallet(f,p,c,trace) for f in FAMILIES for p in POLICIES for c in COSTS}
                rows=db.execute('SELECT e.body,e.entry_time FROM events e JOIN selected s ON e.symbol=s.symbol AND e.day=s.day WHERE e.day=? ORDER BY e.entry_time,e.symbol,e.id',(day,))
                count=0
                for body,when in rows:
                    count+=1
                    if count>50000:raise ValueError('DAILY_EVENT_BOUND')
                    e=json.loads(body);at=datetime.fromisoformat(when)
                    try:bars=cache.get(e['symbol'],day)
                    except SourceChanged:raise
                    except (OSError,ValueError,KeyError):
                        for (f,_,_),lane in lanes.items():
                            if f in e['families']:lane.counts['INVALID_OR_MISSING_PRICE_PARTITION']+=1
                        continue
                    e['prefix_ok']=e['prefix_ok'] and normalized_prefix_matches(e.get('prefix_evidence'),bars)
                    prefix_counts['matched' if e['prefix_ok'] else 'unverified_or_mismatched']+=1
                    for (f,_,_),lane in lanes.items():
                        if f in e['families']:lane.consider(e,bars,at)
                for key,lane in lanes.items():case_days.setdefault(key,[]).append(lane.finish(day))
                progress('REPLAY_DATE='+day,flush=True)
        cache.verify_stable()
        if read_json(repo/REPORT,MAX_REPORT)[1]!=rhash:raise ValueError('REPORT_CHANGED')
        common={f:set.intersection(*({d['day'] for d in days if d['fully_resolved'] and d['input_complete'] and d.get('signal_evidence_complete',True)}
            for (family,_,_),days in case_days.items() if family==f)) for f in FAMILIES} if case_days else {}
        cases=[dict(family=f,policy=p,cost_per_share_per_side=c,**aggregate(days),
            paired_common_day_metrics=aggregate([d for d in days if d['day'] in common[f]]),
            months={m:aggregate([d for d in days if d['day'].startswith(m)]) for m in ('2026-06','2026-07','2026-08')})
            for (f,p,c),days in case_days.items()]
        for (f,p,c),days in case_days.items():
            (output/f'private-days-{f}-{p}-{c}.json').write_text(json.dumps(days,default=str),encoding='utf-8')
        entries=sum(c['counts'].get('entries',0) for c in cases)
        evidence_status=('PRICE_REPLAY_WITH_ENTRIES' if entries else
            'NO_VALIDATED_SIGNAL_PREFIXES' if prefix_counts['matched']==0 else 'NO_ENTRIES_AFTER_OTHER_FILTERS')
        result=dict(evidence_status=evidence_status,prefix_validation=dict(prefix_counts),common_resolved_dates={f:sorted(dates) for f,dates in common.items()},scope=VERSION,plan_sha256=sha256((output/'frozen-plan.json').read_bytes()).hexdigest(),
            source_report_sha256=rhash,corpus=provenance,price_partitions_read=len(cache.proofs),cases=cases,
            promotion='NONE',orders=0,network_requests=0,grok_requests=0,execution_authorized=False,
            limitations=['Sampled historical case study, selected after reviewing family aggregates; not fresh holdout or full quarter portfolio',
                'Stored detector labels reused; prefix bar timestamps checked but detection code and features not independently regenerated',
                'Each family/policy/cost case has its own daily $1000 wallet; accounts reset between days, not a continuous investment return',
                'Unresolved days excluded from headline; their conditional closed results and unresolved counts remain visible',
                'OHLC opens/ranges are not executable bid/ask; no queue, halt, latency or actual fill verification',
                '1% prior-minute volume is a liquidity proxy, not displayed order-book size',
                'Flat per-share cost assumptions are not verified account costs; target R is price risk before costs',
                'Partial proceeds held until full exit; no same-symbol overlap and at most two positions within a daily case',
                'No new strategy qualification or changes to Warrior, Scalper, protective exits or Grok'])
        (output/'private-price-inputs.json').write_text(json.dumps({f'{s}_{d}':p for (s,d),p in cache.proofs.items()}),encoding='utf-8')
        path=output/'episode-price-study-summary.json';path.write_text(json.dumps(result,default=str,indent=2),encoding='utf-8')
        progress('EVIDENCE_STATUS='+evidence_status,flush=True)
        progress('UPLOAD_SUMMARY='+str(path),flush=True)
        return result
    except Exception as exc:
        (output/'failure.json').write_text(json.dumps(dict(status='FAILED',error_type=type(exc).__name__,
            reason=str(exc) if type(exc) is ValueError else 'INPUT_OR_DATABASE_FAILURE',orders=0)),encoding='utf-8')
        raise
    finally:db.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();run(a.repo,a.output)


if __name__=='__main__':main()

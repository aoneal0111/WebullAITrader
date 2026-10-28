"""Offline replay of recorded Scalper policy inputs; no providers or execution."""
from collections import Counter
from dataclasses import fields,replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import argparse
import json
from pathlib import Path
from typing import get_args,get_type_hints

from .daily_research_journal import canonical,verify_journal

D=Decimal
POLICY_FILES=('app/strategies/warrior_momentum/quick_scalper.py',
    'app/strategies/warrior_momentum/quick_scalper_runtime.py')


def typed(cls,raw):
    """Exact dataclass schema and original decimal/time types, never retiming."""
    names={f.name for f in fields(cls)}
    if not isinstance(raw,dict) or set(raw)!=names:raise ValueError('RECORDED_SCHEMA_CHANGED')
    hints=get_type_hints(cls);values={}
    for name,value in raw.items():
        hint=hints[name];options=get_args(hint) or (hint,)
        scalar=next((item for item in options if item is not type(None)),hint)
        if value is None:
            if type(None) not in options:raise ValueError('NULL_REQUIRED_FIELD')
            values[name]=None
        elif Decimal in options:
            if not isinstance(value,str):raise ValueError('EXACT_DECIMAL_STRING_REQUIRED')
            parsed=D(value)
            if not parsed.is_finite():raise ValueError('NONFINITE_INPUT')
            values[name]=parsed
        elif datetime in options:
            parsed=datetime.fromisoformat(value)
            if parsed.utcoffset() is None:raise ValueError('AWARE_TIME_REQUIRED')
            values[name]=parsed
        elif getattr(hint,'__origin__',None) is frozenset:
            if not isinstance(value,list) or any(type(x) is not str for x in value):raise ValueError('EXACT_SESSION_LIST_REQUIRED')
            values[name]=frozenset(value)
        else:
            if scalar not in (int,bool,str) or type(value) is not scalar:raise ValueError('EXACT_SCALAR_TYPE_REQUIRED')
            values[name]=value
    return cls(**values)


def reconstructed_preview(source,quote,at):
    # Identical to the current native replace() in _authorize_and_submit.
    return replace(source,decision_at=at,observed_at=at,
        last=D(quote['last']),bid=D(quote['bid']),ask=D(quote['ask']),
        last_timestamp=datetime.fromisoformat(quote['last_timestamp']),
        bid_timestamp=datetime.fromisoformat(quote['bid_timestamp']),
        ask_timestamp=datetime.fromisoformat(quote['ask_timestamp']),
        observed_bid_advance=None if source.observed_bid_advance is None else source.observed_bid_advance+D(quote['bid'])-source.bid,
        observed_ask_advance=None if source.observed_ask_advance is None else source.observed_ask_advance+D(quote['ask'])-source.ask)


def edge_terms(snapshot,policy):
    result=policy.assess(snapshot);c=policy.config
    terms=dict(absolute_minimum=c.absolute_minimum_move,
        range_fraction=snapshot.short_horizon_range*c.volatility_move_fraction,
        cost_multiple=result.round_trip_cost*c.execution_cost_multiple,
        stop_risk_plus_cost=(snapshot.ask-snapshot.structural_stop)*result.required_net_target_reward_r+result.round_trip_cost)
    return result,dict(spread=snapshot.ask-snapshot.bid,stop_distance=snapshot.ask-snapshot.structural_stop,
        short_horizon_range=snapshot.short_horizon_range,
        observed_bid_advance=snapshot.observed_bid_advance,observed_ask_advance=snapshot.observed_ask_advance,
        target_move=result.target_move,round_trip_cost=result.round_trip_cost,
        modeled_slippage_both_sides=result.round_trip_cost-(snapshot.ask-snapshot.bid),
        ask_anchored_target_net_before_fees=result.target_move-(result.round_trip_cost-(snapshot.ask-snapshot.bid)),
        policy_target_net=result.target_move-result.round_trip_cost,
        extra_spread_reserve_in_policy_net=snapshot.ask-snapshot.bid,
        expected_move=result.expected_move,required_bid_move=result.required_bid_move,
        edge_margin=result.expected_move-result.required_bid_move,
        momentum_confidence=result.momentum_confidence,required_reward_r=result.required_net_target_reward_r),terms


def book_state(at,bid_at,ask_at):
    ages=[D(str((at-t).total_seconds())) for t in (bid_at,ask_at)]
    return 'FUTURE' if min(ages)<0 else 'STALE' if max(ages)>5 else 'FRESH'


def audit(session):
    from app.strategies.warrior_momentum.quick_scalper import QuickScalpSnapshot,QuickScalperConfig,QuickScalperPolicy
    root=Path(session).resolve();verified=verify_journal(root/'journal')
    manifest=json.loads((root/'research-manifest.json').read_text())
    summary=json.loads((root/'research-summary.json').read_text())
    if (manifest.get('scope')!='NORMAL_DESKTOP_DAILY_RESEARCH_V1' or summary.get('policy_unchanged') is not True
            or manifest['baseline_sha256']!=verified['baseline_sha256']):raise ValueError('FROZEN_DAILY_BASELINE_REQUIRED')
    source_root=Path(__file__).resolve().parents[2]
    for name in POLICY_FILES:
        if sha256((source_root/name).read_bytes()).hexdigest()!=manifest['source_files'].get(name):
            raise ValueError('RECORDED_NATIVE_POLICY_SOURCE_MISMATCH')
    stats={};initial=Counter();confirmed=Counter();pairs=Counter();dominant=Counter();symbols=Counter()
    closest=[];mismatches=[];policy=None;previous='0'*64;sequence=0
    def metric(name,value):
        if value is None:return
        item=stats.setdefault(name,dict(count=0,min=value,max=value,total=D(0)))
        item['count']+=1;item['min']=min(item['min'],value);item['max']=max(item['max'],value);item['total']+=value
    for path in sorted((root/'journal').glob('events-*.jsonl')):
        with path.open('rb') as stream:
            for line in stream:
                row=json.loads(line);claimed=row.pop('sha256');sequence+=1
                if row['previous_sha256']!=previous or row['sequence']!=sequence or sha256(canonical(row)).hexdigest()!=claimed:
                    raise ValueError('JOURNAL_CHANGED_DURING_REPLAY')
                previous=claimed;p=row['payload']
                if row['kind']=='NATIVE_POLICY_BASELINE':
                    if policy is not None:raise ValueError('MULTIPLE_POLICY_BASELINES')
                    policy=QuickScalperPolicy(typed(QuickScalperConfig,p['settings']['scalper']))
                elif row['kind']=='STREAM_ASSESSMENT':
                    if policy is None:raise ValueError('POLICY_BASELINE_MISSING')
                    snap=typed(QuickScalpSnapshot,p['snapshot']);result,terms,candidates=edge_terms(snap,policy)
                    initial[result.reason]+=1;symbols[snap.symbol]+=1
                    if result.reason!=p['reason'] or result.decision.value!=p['decision']:
                        if len(mismatches)<10:mismatches.append(dict(sequence=sequence,kind='INITIAL_POLICY',recorded=p['reason'],replayed=result.reason))
                    for k,v in terms.items():metric(k,v)
                    for name,value in candidates.items():
                        if value==result.target_move:dominant[name]+=1
                    sample=dict(sequence=sequence,record_sha256=claimed,symbol=snap.symbol,decision_at=snap.decision_at.isoformat(),
                        reason=result.reason,**terms,target_candidates=candidates)
                    if result.reason=='INSUFFICIENT_NET_EXECUTABLE_EDGE':
                        closest.append(sample);closest.sort(key=lambda x:x['edge_margin'],reverse=True);del closest[10:]
                elif row['kind']=='ASSESSMENT' and p['engine']=='QUICK_SCALPER':
                    if policy is None:raise ValueError('POLICY_BASELINE_MISSING')
                    original=typed(QuickScalpSnapshot,p['preview']);at=datetime.fromisoformat(p['assessed_at']);quote=p['quote']
                    snap=reconstructed_preview(original,quote,at);result,terms,_=edge_terms(snap,policy)
                    confirmed[result.reason]+=1
                    expected='ENTRY_CANDIDATE' if result.decision.value=='EXECUTABLE' else 'WAIT' if result.decision.value=='WAIT' else 'BLOCKED'
                    if expected!=p['baseline_result'] and len(mismatches)<10:
                        mismatches.append(dict(sequence=sequence,kind='CONFIRMATION',recorded=p['baseline_result'],replayed=expected))
                    stream_state=book_state(original.decision_at,original.bid_timestamp,original.ask_timestamp)
                    rest_state=book_state(at,snap.bid_timestamp,snap.ask_timestamp)
                    pairs['STREAM_'+stream_state+'_REST_'+rest_state]+=1
                    if original.bid==snap.bid and original.ask==snap.ask:pairs['same_book_prices']+=1
                    for side in ('bid','ask'):
                        old=getattr(original,side+'_timestamp');new=getattr(snap,side+'_timestamp')
                        advance=D(str((new-old).total_seconds()));metric('rest_'+side+'_advance_seconds',advance)
                        pairs[side+'_'+('OLDER' if advance<0 else 'NEWER' if advance>0 else 'SAME')]+=1
                        metric('rest_'+side+'_age_seconds',D(str((at-new).total_seconds())))
                    # This is a quote-safety classification only. It cannot authorize
                    # an entry, and stale previews cannot be rescued by preferring them.
                    if min(snap.bid_timestamp,snap.ask_timestamp)<min(original.bid_timestamp,original.ask_timestamp):
                        pairs['REST_BOOK_REGRESSED']+=1
                        if book_state(at,original.bid_timestamp,original.ask_timestamp)=='FRESH':
                            pairs['FRESH_STREAM_AT_CONFIRMATION_WITH_REGRESSED_REST']+=1
                    metric('confirmation_latency_seconds',D(str((at-original.decision_at).total_seconds())))
                    metric('confirmed_edge_margin',terms['edge_margin'])
    if previous!=verified['chain_head'] or sequence!=verified['records']:raise ValueError('JOURNAL_CHANGED_DURING_REPLAY')
    if policy is None:raise ValueError('POLICY_BASELINE_MISSING')
    # Old code is replayed exactly; no guessed costs, hypothetical repricing,
    # new quote requests, threshold tuning, broker calls or model inference.
    return dict(scope='OFFLINE_RECORDED_SCALPER_EDGE_AND_QUOTE_AUDIT_V1',
        baseline_sha256=verified['baseline_sha256'],journal_chain_head=verified['chain_head'],
        evidence_complete=verified['evidence_complete'],native_policy_source_verified=True,
        source_replay_matches=not mismatches,replay_mismatches=mismatches,
        stream_assessments=sum(initial.values()),initial_reasons=dict(initial),
        confirmations=sum(confirmed.values()),confirmation_reasons=dict(confirmed),
        paired_quotes=dict(pairs),target_dominant_terms=dict(dominant),
        measurements={k:dict(count=v['count'],min=v['min'],max=v['max'],mean=v['total']/v['count']) for k,v in stats.items()},
        most_observed_symbols=[dict(symbol=symbol,count=count) for symbol,count in symbols.most_common(12)],
        closest_edge_examples=closest,network_requests=0,grok_requests=0,orders=0,baseline_modified=False,
        limitations=['Policy replay and arithmetic attribution, not a strategy profitability backtest',
            'Dominant target terms can tie; counts are not mutually exclusive',
            'Repeated snapshots are not independent trade opportunities',
            'Demonstrated movement is past evidence, not proof of remaining future movement',
            'Provider timestamps are preserved; receive time is not a replacement for book time',
            'Snapshot/depth entitlement and raw provider timestamp semantics are not verified by this journal',
            'A regressed REST book does not make a stale stream book executable',
            'Quote quality and entry policy need separate qualification before changing execution'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=audit(args.session)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,default=str,indent=2,allow_nan=False)
    print('SCALPER_AUDIT=COMPLETE' if result['source_replay_matches'] else 'SCALPER_AUDIT=REPLAY_MISMATCH_REVIEW_REQUIRED')
    print('UPLOAD_SUMMARY='+str(args.output.resolve()))


if __name__=='__main__':main()

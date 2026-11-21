"""Stream a compact read-only summary of persistent PAPER model accounting."""
from collections import Counter, deque
import json
from pathlib import Path
from app.read_only_sqlite import read_database


def summarize(path, *, recent_limit=500):
    from .paper_day_review import call_reviews
    path=Path(path)
    if not 1<=recent_limit<=500 or path.is_symlink() or not path.is_file() or path.stat().st_size>512*1024**2:
        raise ValueError('BOUNDED_PAPER_CALL_LEDGER_REQUIRED')
    roles=Counter();states=Counter();actions=Counter();failures=Counter();recent=deque(maxlen=recent_limit)
    usage={};unknown_usage=0;total=0;invalid=0;unfinalized=0;first=None;last=None
    with read_database(path) as db:
        db.execute('BEGIN')
        for row in db.execute('SELECT engine,stage,started,context,result FROM calls ORDER BY rowid'):
            total+=1
            if total>100000 or sum(len(str(v).encode()) for v in row)>2*1024**2:
                raise ValueError('COMPACT_CALL_REVIEW_BOUND')
            engine,stage,started,context,raw=row
            roles[str(engine)+'|'+str(stage)]+=1
            if first is None:first=started
            last=started
            review=call_reviews([row])[0]
            if review.get('reason') is not None:review['reason']=review['reason'][:1000]
            review['candidates']=review['candidates'][:32]
            if isinstance(review.get('selected_ids'),list):
                review['selected_ids_truncated']=len(review['selected_ids'])>32
                review['selected_ids']=review['selected_ids'][:32]
            recent.append(review)
            states[str(review['status'])[:80]]+=1
            actions[str(review.get('action') or 'UNSPECIFIED')[:80]]+=1
            if review.get('failure_category'):failures[str(review['failure_category'])[:80]]+=1
            if review['result_decode_status']=='INVALID_JSON_OBJECT':invalid+=1
            if not raw:unfinalized+=1
            try:
                result=json.loads(raw) if raw else {}
                if not isinstance(result,dict):continue
                identity=result.get('usage_scope');counter=result.get('usage_cumulative_not_per_request')
                if isinstance(identity,str) and isinstance(counter,dict) and all(type(counter.get(k)) is int and counter[k]>=0 for k in ('input_tokens','output_tokens')):
                    old=usage.setdefault(identity,{'input_tokens':0,'output_tokens':0})
                    for key in old:old[key]=max(old[key],counter[key])
                else:unknown_usage+=1
            except (ValueError,TypeError):continue
    return dict(scope='COMPACT_PERSISTENT_PAPER_CALL_REVIEW_V1',total_calls=total,roles=dict(roles),
        statuses=dict(states),actions=dict(actions),failures=dict(failures),invalid_results=invalid,
        unfinalized_calls=unfinalized,first_call_at=first,last_call_at=last,
        usage_scope_count=len(usage),recorded_tokens={k:sum(v[k] for v in usage.values()) for k in ('input_tokens','output_tokens')},
        rows_without_attributed_usage=unknown_usage,recent_reviews=list(recent),recent_review_limit=recent_limit,
        raw_prompts_or_database_exported=False,
        limitations=['Counts cover this persistent operation across restarts; recent details are a bounded tail.',
            'Token counts use maxima per cumulative usage identity, never the sum of cumulative snapshots.',
            'Missing or unattributed usage cannot establish actual API charges; dollar cost is unknown.',
            'Model BUY/PASS/EXIT opinions are not fills or trading outcomes.'])

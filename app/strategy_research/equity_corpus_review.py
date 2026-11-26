"""Read-only review of the 23-family report and pinned external research sources.

This is evidence triage, not a strategy backtest, ranking or release gate.
"""
import argparse
from collections import Counter
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from app.equity_overhaul.metric_semantics_audit import cohort

REPORT = Path('data/research/historical_tranches/2026_06_01__2026_08_31/reports/full_research_final.json')
LOCK = Path(__file__).with_name('equity_repository_sources.json')
MAX_REPORT = 40_000_000


def _unique_pairs(pairs):
    result = {}
    for key,value in pairs:
        if key in result: raise ValueError('DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def read_json(path,limit):
    with Path(path).open('rb') as stream: raw=stream.read(limit+1)
    if len(raw)>limit: raise ValueError('INPUT_SIZE_LIMIT')
    def invalid(_): raise ValueError('NONFINITE_JSON_NUMBER')
    return json.loads(raw,parse_float=Decimal,parse_constant=invalid,object_pairs_hook=_unique_pairs),sha256(raw).hexdigest()


def registry():
    lock,_=read_json(LOCK,200_000)
    repos=lock['repositories'];mapping=lock['strategy_mapping']
    ids={r['id'] for r in repos}
    if (len(ids)!=len(repos) or len(mapping)!=23 or len({m['strategy'] for m in mapping})!=23
            or lock.get('execution_authorized') is not False):raise ValueError('INVALID_RESEARCH_REGISTRY')
    for r in repos:
        if not re.fullmatch(r'[0-9a-f]{40}',r['revision']):raise ValueError('PINNED_SOURCE_REVISION_REQUIRED')
        if r['source_code_imported'] is not False or r['execution_authorized'] is not False:
            raise ValueError('REFERENCE_ONLY_REGISTRY_REQUIRED')
        for f in r['source_files']:
            if not re.fullmatch(r'[0-9a-f]{64}',f['sha256']):raise ValueError('SOURCE_DIGEST_REQUIRED')
    for m in mapping:
        if not set(m['repository_ids'])<=ids or m['exact_parity_verified'] is not False:
            raise ValueError('INVALID_STRATEGY_MAPPING')
    return lock


def count(value):
    if type(value) is not int or value<0:raise ValueError('NONNEGATIVE_SAMPLE_COUNT_REQUIRED')
    return value


def _case(mapping,key):
    found=[mapping[k] for k in (key,key.lower(),key.title()) if k in mapping]
    if len(found)>1:raise ValueError('AMBIGUOUS_SPLIT_KEYS')
    return found[0] if found else None


def split_card(temporal,name,split):
    # Never substitute execution-analysis or whole-sample counts for a split.
    current=(_case(temporal.get('splits',{}),split) or {}).get(name)
    if current is None:current=_case(temporal.get('strategy_scorecards',{}).get(name,{}),split)
    if current is not None and not isinstance(current,dict):raise ValueError('INVALID_SPLIT_CARD')
    return current


def descriptive_cohort(label,row):
    if row is None:return dict(cohort=label,status='MISSING',sample_count=None,realized_win_rate=None)
    return cohort(label,row)


def review_report(report,lock=None):
    lock=registry() if lock is None else lock
    expected={m['strategy'] for m in lock['strategy_mapping']}
    if not isinstance(report,dict) or report.get('metadata',{}).get('research_only') is not True:
        raise ValueError('RESEARCH_ONLY_REPORT_REQUIRED')
    cards=report.get('strategy_scorecards')
    if not isinstance(cards,dict) or set(cards)!=expected:raise ValueError('EXACT_23_STRATEGIES_REQUIRED')
    temporal=report.get('temporal',{});rows=[]
    for mapping in lock['strategy_mapping']:
        name=mapping['strategy'];card=cards[name];whole=count(card['sample_count'])
        splits={s:split_card(temporal,name,s) for s in ('TRAIN','VALIDATION','TEST')}
        split_counts={s:None if c is None else count(c['sample_count']) for s,c in splits.items()}
        counts_known=all(n is not None for n in split_counts.values())
        count_status=('MATCH' if sum(split_counts.values())==whole else 'MISMATCH') if counts_known else 'MISSING_SPLIT_COUNTS'
        stability=temporal.get('policy_stability',{}).get(name,{})
        rwhole=report.get('r_multiple_research',{}).get(name,{})
        # Eventual touch and stop-first marginals may overlap. Their difference
        # is a bound, not realized wins, independent trade count or expectancy.
        cohorts=[descriptive_cohort('WHOLE_0.5R',rwhole.get('0.5')),
                 descriptive_cohort('WHOLE_1R',rwhole.get('1')),
                 descriptive_cohort('WHOLE_2R',rwhole.get('2'))]
        cohorts += [descriptive_cohort(s+'_2R',(_case(stability,s) or {}).get('2R')) for s in splits]
        rows.append(dict(**mapping,whole_sample_memberships=whole,split_sample_memberships=split_counts,
            split_count_status=count_status,zero_observations=whole==0,
            descriptive_cohorts=cohorts,realized_net_win_rate=None,net_expectancy=None,
            review_status='NO_OBSERVATIONS' if whole==0 else 'CHRONOLOGICAL_FILL_AND_COST_REPLAY_REQUIRED'))
    return dict(scope='OFFLINE_23_STRATEGY_AND_REPOSITORY_EVIDENCE_REVIEW_V1',
        strategy_count=23,total_strategy_memberships=sum(r['whole_sample_memberships'] for r in rows),
        unique_trade_count=None,strategies_with_no_observations=[r['strategy'] for r in rows if r['zero_observations']],
        split_count_mismatches=[r['strategy'] for r in rows if r['split_count_status']=='MISMATCH'],
        repositories=[dict(id=r['id'],repository=r['repository'],revision=r['revision'],
            verification=r['verification'],author_evidence=r['author_evidence'],source_findings=r['source_findings'],
            existing_atlas_research_adapters=r['existing_atlas_research_adapters']) for r in lock['repositories']],
        strategies=rows,rankings=[],orders=0,network_requests=0,grok_requests=0,
        execution_authorized=False,promotion='NONE',
        next_stage='Inspect available episode/bar schemas; freeze comparable causal rules and costs before replay',
        limitations=['This report is not a new PnL backtest and cannot establish winning strategies',
            'Strategy memberships can overlap the same episode and are not independent trades',
            'TEST is an existing historical label, not a newly untouched holdout',
            'Aggregate target-touch and stop-first rates cannot recover exact fill order or net profitability',
            'Repository family relationships do not establish detector parity or transferable author performance',
            'No third-party code imported, dependencies installed, account queried or trading baseline modified'])


def inventory(tranche,max_entries=10000):
    """Metadata only. Bounded enumeration, no SQLite opens or price extraction."""
    tranche=Path(tranche);counts=Counter();examples=[];seen=0;bounded=False
    for dirname,dirs,files in os.walk(tranche,followlinks=False):
        current=Path(dirname);depth=len(current.relative_to(tranche).parts)
        safe=[]
        for name in sorted(dirs):
            seen+=1
            if seen>max_entries:bounded=True;break
            if not (current/name).is_symlink() and depth<3:safe.append(name)
        dirs[:]=safe
        if bounded:break
        for name in sorted(files):
            seen+=1
            if seen>max_entries:bounded=True;break
            path=current/name
            if path.is_symlink() or path.suffix.lower() not in ('.json','.jsonl','.sqlite3','.parquet','.csv'):continue
            counts[path.suffix.lower()]+=1
            # Examples are evidence schemas/receipts, never .env or raw values.
            if len(examples)<30 and (name in ('episodes.jsonl','manifest.json','run_manifest.json','full_research_final.json')
                    or 'corpus' in path.relative_to(tranche).parts):
                examples.append(dict(relative_path=path.relative_to(tranche).as_posix(),bytes=path.stat().st_size))
        if bounded:break
    return dict(entries_examined=min(seen,max_entries),enumeration_capped=bounded,
        file_counts=dict(counts),examples=examples,prices_read=False,databases_opened=0)


def run(repo,output,report_path=None):
    repo=Path(repo).resolve();source=repo/REPORT if report_path is None else Path(report_path).resolve()
    report,digest=read_json(source,MAX_REPORT);lock=registry()
    result=review_report(report,lock)
    result.update(source_report_sha256=digest,registry_sha256=sha256(LOCK.read_bytes()).hexdigest(),
                  local_evidence_inventory=inventory(source.parent.parent))
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'equity-23-repository-review.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    # A short view is convenient for pasting; detailed bounds stay in the file.
    compact={k:result[k] for k in ('scope','strategy_count','total_strategy_memberships',
        'strategies_with_no_observations','split_count_mismatches','promotion','orders','network_requests')}
    compact['repository_count']=len(lock['repositories'])
    print(json.dumps(compact,indent=2))
    print('UPLOAD_SUMMARY='+str(output/'equity-23-repository-review.json'))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--report',type=Path)
    a=p.parse_args();run(a.repo,a.output,a.report)


if __name__=='__main__':main()

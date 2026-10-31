"""Diagnose aggregate target-hit semantics; no replay, ranking, or order authority."""
import argparse
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import zipfile

MAX_REPORT_BYTES = 40_000_000
ZERO = Decimal(0)
ONE = Decimal(1)


def rate(value):
    if isinstance(value, bool):
        raise ValueError('INVALID_PROBABILITY')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('INVALID_PROBABILITY') from None
    if not result.is_finite() or not ZERO <= result <= ONE:
        raise ValueError('INVALID_PROBABILITY')
    return result


def marginal_bounds(hit, stop):
    """Bounds on H intersection not-S, when H/S have the same denominator.

    H is eventual target touch, S is stop-first. These are not disjoint outcomes.
    Bounds describe a population, not independent trials or realized net wins.
    """
    h, s = rate(hit), rate(stop)
    return dict(eventual_target_hit_rate=str(h), stop_first_rate=str(s),
        forced_overlap_lower_bound=str(max(ZERO,h+s-ONE)),
        target_hit_without_stop_first_lower_bound=str(max(ZERO,h-s)),
        target_hit_without_stop_first_upper_bound=str(min(h,ONE-s)))


def cohort(label, values):
    if not isinstance(values,dict):
        raise ValueError('INVALID_COHORT')
    count=values.get('sample_count')
    if isinstance(count,bool) or not isinstance(count,int) or count<0:
        raise ValueError('INVALID_SAMPLE_COUNT')
    result=dict(cohort=label,sample_count=count,realized_win_rate=None,
        metrics_are_trade_win_rates=False)
    if not count or values.get('hit_rate') is None or values.get('stop_first_rate') is None:
        return dict(result,status='INSUFFICIENT_AGGREGATE_EVIDENCE')
    return dict(result,status='DESCRIPTIVE_BOUNDS_ONLY',
        **marginal_bounds(values['hit_rate'],values['stop_first_rate']))


def audit_report(report):
    if not isinstance(report,dict) or report.get('metadata',{}).get('research_only') is not True:
        raise ValueError('EXPECTED_RESEARCH_REPORT')
    whole=report['r_multiple_research']
    temporal=report['temporal']['policy_stability']
    cards=report['strategy_scorecards']
    if (not isinstance(cards,dict) or not cards or len(cards)>100
            or set(whole)!=set(cards) or set(temporal)!=set(cards)):
        raise ValueError('STRATEGY_MEMBERSHIP_MISMATCH')
    results=[]
    for strategy in sorted(cards):
        observations=[]
        for target in ('0.5','1','2'):
            observations.append(cohort('WHOLE_SAMPLE_'+target+'R',whole[strategy][target]))
        for split in ('TRAIN','VALIDATION','TEST'):
            observations.append(cohort(split+'_2R',temporal[strategy][split]['2R']))
        results.append(dict(strategy=strategy,cohorts=observations))
    return dict(scope='OFFLINE_HISTORICAL_METRIC_SEMANTICS_AUDIT_V1',
        strategies=len(results),results=results,network_requests=0,orders=0,
        execution_authorized=False,promotion='NONE',rankings=[],
        denominator_assumption='Hit and stop-first flags refer to the same cohort and target; see source analysis.py SQL AVG columns',
        limitations=[
            'Bounds are not estimated stop-aware win rates or confidence intervals',
            'Aggregates cannot recover exact overlap, chronological fills, net profit, gaps or portfolio exposure',
            'Missing outcomes and ambiguous candles can prevent even a target-without-stop-first event from being a valid completed trade',
            'Previously examined TEST dates are descriptive evidence, not a fresh selection holdout',
            'Overlapping strategy memberships are not independent trades and must not be summed',
            'Whole-sample 0.5R statistics are not evidence of a five-minute Scalper exit or spread-adjusted profitability',
            'Historical strategy labels are not equivalent to the new pre-HOD entry hypothesis',
            'No automatic strategy ranking, qualification, production change or promotion'])


def audit_archive(path):
    path=Path(path)
    if path.stat().st_size>MAX_REPORT_BYTES:
        raise ValueError('REPORT_SIZE_LIMIT')
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        if (len(entries)!=1 or entries[0].filename!='full_research_final.json'
                or entries[0].file_size>MAX_REPORT_BYTES):
            raise ValueError('EXPECTED_SINGLE_BOUNDED_RESEARCH_REPORT')
        raw=archive.read(entries[0])
    report=json.loads(raw,parse_float=Decimal)
    result=audit_report(report)
    result.update(source_archive_sha256=sha256(path.read_bytes()).hexdigest(),
                  source_report_sha256=sha256(raw).hexdigest())
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    args=parser.parse_args()
    print(json.dumps(audit_archive(args.archive),indent=2))


if __name__=='__main__':
    main()

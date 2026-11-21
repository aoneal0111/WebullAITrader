"""Frozen strategy adaptations, compared together without selecting a winner.

Previously seen dates remain exploratory. No account, provider, GUI or production
selector is accessed. Costs must be explicit; normal and double costs are retained.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal as D
import argparse
import csv
from hashlib import sha256
import io
import json
from pathlib import Path

from .candidates import Bar, Candidate, validate_bars
from .replay import Costs, replay, metrics


@dataclass(frozen=True)
class Adaptation:
    name: str
    candidate: Candidate
    stop_lookback: int
    max_hold: int
    purpose: str

    def __post_init__(self):
        if (not isinstance(self.candidate,Candidate)
                or self.candidate not in {Candidate.IBS_DAILY_REVERSION,Candidate.DONCHIAN20_DAILY}
                or type(self.stop_lookback) is not int or not 5 <= self.stop_lookback <= 50
                or type(self.max_hold) is not int or not 1 <= self.max_hold <= 60):
            raise ValueError('bounded daily repository adaptation required')


VARIANTS = (
    Adaptation('IBS_BASELINE_5_10',Candidate.IBS_DAILY_REVERSION,5,10,'Existing baseline'),
    Adaptation('IBS_STRUCTURE_10_HOLD_5',Candidate.IBS_DAILY_REVERSION,10,5,
               'Longer pre-entry structure with a shorter mean-reversion timeout'),
    Adaptation('DONCHIAN_BASELINE_5_10',Candidate.DONCHIAN20_DAILY,5,10,'Existing baseline'),
    Adaptation('DONCHIAN_STRUCTURE_20_HOLD_30',Candidate.DONCHIAN20_DAILY,20,30,
               'Channel-length structure with a longer trend holding budget'),
)


def _describe(trades):
    result=metrics(trades)
    closed=[t for t in trades if t.reason!='EVALUATION_BOUNDARY']
    n=len(closed)
    result['mean_initial_risk_per_share']=sum((t.entry_price-t.stop for t in closed),D(0))/n if n else None
    result['mean_net_return_bps']=sum((t.net_pnl_per_share/t.entry_price*10000 for t in closed),D(0))/n if n else None
    result['mean_holding_hours']=sum(((datetime.fromisoformat(t.exit_at)-datetime.fromisoformat(t.entry_at)).total_seconds()/3600 for t in closed),0)/n if n else None
    return result


def compare_adaptations(bars, costs, *, split_at):
    if not 0 < len(bars) <= 5000:
        raise ValueError('bounded daily input required')
    validate_bars(bars,'daily')
    if not isinstance(costs,Costs) or not isinstance(split_at,datetime) or split_at.utcoffset() is None:
        raise ValueError('explicit costs and aware date split required')
    split=next((i for i,b in enumerate(bars) if b.opened>=split_at),len(bars))
    if split < 250 or len(bars)-split < 100 or bars[split-1].closed > split_at:
        raise ValueError('250 development bars and 100 nonoverlapping later bars required')
    stress=Costs(costs.adverse_bps*2,costs.fee_per_share*2)
    results=[]
    for variant in VARIANTS:
        record=asdict(variant)
        settings=dict(timeframe='daily',max_hold=variant.max_hold,stop_lookback=variant.stop_lookback)
        record['development']=_describe(replay(bars,variant.candidate,costs,end=split,**settings))
        record['later_dates']=_describe(replay(bars,variant.candidate,costs,start=split,**settings))
        record['later_dates_double_cost']=_describe(replay(bars,variant.candidate,stress,start=split,**settings))
        results.append(record)
    return dict(scope='EXPLORATORY_FROZEN_ADAPTATIONS_NOT_UNTOUCHED_HOLDOUT',
        execution_authorized=False,decision='RESEARCH_ONLY_NO_AUTOMATIC_SELECTION',
        symbol=bars[0].symbol,split_at=split_at.isoformat(),costs_per_side=asdict(costs),
        variants_tested=len(results),results=results,
        limitations=['The existing dates have already been inspected; later dates are not a fresh final holdout.',
                     'Wider initial stops need smaller share quantities for equal monetary risk.',
                     'One-unit/equal-trade R summaries are not portfolio P&L or capacity.',
                     'Boundary marks and unresolved positions remain visible and excluded from headline win rates.',
                     'Longer holds change overnight, gap and capital exposure; this is not a scalper replacement.',
                     'Costs are assumptions until independently measured; no quote or fill proof is supplied.',
                     'A higher win rate alone does not establish positive expectancy or acceptable tail risk.'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv',type=Path)
    parser.add_argument('--split-at',type=datetime.fromisoformat,required=True)
    parser.add_argument('--adverse-bps-per-side',type=D,required=True)
    parser.add_argument('--fee-per-share-per-side',type=D,required=True)
    args=parser.parse_args(argv)
    try:
        if args.csv.stat().st_size>8*1024*1024:
            raise ValueError('input byte limit')
        raw=args.csv.read_bytes()
        if len(raw)>8*1024*1024:
            raise ValueError('input byte limit')
        bars=[]
        for row in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
            if len(bars)>=5000:
                raise ValueError('input row limit')
            bars.append(Bar(row['symbol'],datetime.fromisoformat(row['opened']),datetime.fromisoformat(row['closed']),
                *(D(row[k]) for k in ('open','high','low','close','volume')),datetime.fromisoformat(row['session_end'])))
        report=compare_adaptations(tuple(bars),Costs(args.adverse_bps_per_side,args.fee_per_share_per_side),split_at=args.split_at)
        report['input_sha256']=sha256(raw).hexdigest()
        report['implementation_sha256']=sha256(b''.join(Path(__file__).with_name(n).read_bytes()
            for n in ('adaptations.py','candidates.py','replay.py'))).hexdigest()
        print(json.dumps(report,default=str,indent=2))
        return 0
    except (ValueError,KeyError,OSError,ArithmeticError):
        print('ADAPTATION_SCREEN_FAILED')
        return 2


if __name__=='__main__':raise SystemExit(main())

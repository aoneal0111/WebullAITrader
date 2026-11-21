"""Offline EMA seed-sensitivity study using Atlas's native direction function.

No external framework, network, orders, model calls or strategy activation.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.asset_modules.contract_logic import Bar, direction

WINDOWS=(22,32,48,96,192,384)


def analyze(bars, *, now):
    bars=tuple(bars)
    if (not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None
            or not 22<=len(bars)<=1000 or any(not isinstance(b,Bar) for b in bars)):
        raise ValueError('BOUNDED_TYPED_HISTORY_AND_AWARE_CUTOFF_REQUIRED')
    if any(b.end>now for b in bars):
        raise ValueError('FUTURE_BAR_REFUSED')
    if (any(b.end-a.end!=timedelta(minutes=1) for a,b in zip(bars,bars[1:]))
            or now-bars[-1].end>timedelta(minutes=1)):
        raise ValueError('CURRENT_CONTIGUOUS_MINUTE_HISTORY_REQUIRED')
    baseline=direction(bars,now)
    cells=[]
    for window in WINDOWS:
        if len(bars)<window:
            cells.append(dict(bars=window,status='INSUFFICIENT_HISTORY',direction=None,
                              differs_from_supplied_full_history=None))
        else:
            value=direction(bars[-window:],now)
            cells.append(dict(bars=window,status='ASSESSED',direction=value,
                              differs_from_supplied_full_history=value!=baseline))
    sensitive=any(c['differs_from_supplied_full_history'] is True for c in cells)
    return dict(scope='OFFLINE_NATIVE_CRYPTO_WARMUP_STUDY_V1',
        supplied_history_bars=len(bars),native_worker_maximum_bars=48,
        supplied_full_history_direction=baseline,
        status='TRUNCATION_SENSITIVE' if sensitive else 'NO_DIRECTION_DIFFERENCE_ON_SUPPLIED_PATH',
        latest_completed_at=bars[-1].end.isoformat(),decision_cutoff=now.isoformat(),
        cells=cells,orders=0,network_requests=0,grok_requests=0,
        execution_authorized=False,promotion='NONE',
        limitations=[
            'Direction comparison only; no win rate, profitability, fills or fee simulation',
            'The longest supplied history is a reference, not a proven correct seed or optimal window',
            'Availability at the cutoff must be established separately for actual provider history',
            'No difference on one path does not establish universal indicator convergence',
            'No runtime buffer, worker contract, EMA formula or entry settings are changed'])


def synthetic_case():
    now=datetime(2026,10,25,15,0,tzinfo=UTC)
    # Invented downturn, pause and small local breakout; no market performance.
    prices=[D(110)]*144+[D(100)]*47+[D('100.02')]
    bars=tuple(Bar(now-timedelta(minutes=len(prices)-i-1),p+D('.01'),p-D(1),p)
               for i,p in enumerate(prices))
    report=analyze(bars,now=now)
    report['evidence_kind']='SYNTHETIC_STRESS_PATH_NOT_MARKET_DATA'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic',action='store_true',required=True)
    parser.parse_args()
    print(json.dumps(synthetic_case(),indent=2))


if __name__=='__main__':main()

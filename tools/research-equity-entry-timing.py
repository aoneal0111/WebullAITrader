"""Small-output history cross-check plus fixed early Momentum/partial-exit comparison."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.equity_overhaul.history_crosscheck import run_crosscheck
from app.equity_overhaul.entry_timing_screen import run_screen


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('history','report','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('output exists; use a new filename')
    try:
        audit=run_crosscheck(a.history,a.report,a.output.with_suffix('.source'))
        research=run_screen(a.history,a.report)
    except (ValueError,KeyError,TypeError,OSError,ArithmeticError):
        print('ENTRY_TIMING_STATUS=INPUT_VALIDATION_FAILED');return 2
    result=dict(scope='HISTORY_AND_EARLY_MOMENTUM_RESEARCH_V1',execution_authorized=False,promotion='NONE',
        history_diagnostic=audit,entry_timing=research)
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print('ENTRY_TIMING_STATUS=EXPLORATORY_ONLY; PROMOTION=NONE')
    print('UPLOAD_SUMMARY='+str(a.output.resolve()))
    return 0


if __name__=='__main__':raise SystemExit(main())

"""Compare saved SIP minute aggregates with a second vendor's public daily bars."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.equity_overhaul.history_crosscheck import run_crosscheck


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('history','report','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('output exists; use a new filename')
    try:r=run_crosscheck(a.history,a.report,a.output.with_suffix('.source'))
    except (ValueError,KeyError,TypeError,OSError,ArithmeticError):
        print('HISTORY_STATUS=INPUT_VALIDATION_FAILED');return 2
    with a.output.open('x',encoding='utf-8') as f:json.dump(r,f,indent=2)
    print('HISTORY_STATUS='+r['status']+'; PROMOTION=NONE')
    print('UPLOAD_SUMMARY='+str(a.output.resolve()))
    return 0


if __name__=='__main__':raise SystemExit(main())

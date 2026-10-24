"""Run the fixed exploratory V2 hypotheses locally, without API credentials."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.equity_overhaul.v2_screen import run_screen


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():p.error('output exists; preserve earlier experiments')
    try:r=run_screen(a.history,a.report)
    except (ValueError,KeyError,TypeError,OSError,ArithmeticError):
        print('V2_STATUS=INPUT_VALIDATION_FAILED');return 2
    with a.output.open('x',encoding='utf-8') as f:json.dump(r,f,indent=2)
    print('V2_STATUS=EXPLORATORY_ONLY; PROMOTION=NONE')
    print('UPLOAD_SUMMARY='+str(a.output.resolve()))


if __name__=='__main__':raise SystemExit(main())

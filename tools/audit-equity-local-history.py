"""Audit existing minute history locally; output a small summary without bars."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.equity_overhaul.local_audit import run_local_audit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists(): p.error('output exists; preserve earlier audits')
    try:
        result = run_local_audit(args.history, args.report)
    except (ValueError, KeyError, TypeError, OSError, ArithmeticError):
        print('AUDIT_STATUS=INPUT_VALIDATION_FAILED; preserve original files'); return 2
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, indent=2)
    print('AUDIT_STATUS='+result['status'])
    print('AUDIT_SUMMARY='+str(args.output.resolve()))
    return 0 if result['mismatched_cells'] == 0 else 3


if __name__ == '__main__':
    raise SystemExit(main())

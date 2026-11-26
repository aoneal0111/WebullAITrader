"""Freeze a reviewable plan, then evaluate normalized bars entirely offline."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.equity_overhaul.validation import frozen_plan, evaluate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    freeze = sub.add_parser('freeze'); freeze.add_argument('--plan', type=Path, required=True)
    collect = sub.add_parser('capture-alpaca')
    collect.add_argument('--plan', type=Path, required=True)
    collect.add_argument('--output', type=Path, required=True)
    run = sub.add_parser('run')
    run.add_argument('--plan', type=Path, required=True)
    run.add_argument('--input', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.command == 'freeze':
        with args.plan.open('x', encoding='utf-8') as f:
            json.dump(frozen_plan(), f, indent=2)
        print('FROZEN; no network requests or orders')
    elif args.command == 'capture-alpaca':
        from app.equity_overhaul.history import capture
        credentials = (os.environ.get('APCA_API_KEY_ID', ''), os.environ.get('APCA_API_SECRET_KEY', ''))
        try:
            manifest = capture(args.output, json.loads(args.plan.read_text(encoding='utf-8')), credentials)
        except ValueError as exc:
            # Only locally defined fixed validation messages; never print credentials.
            p.error(str(exc))
        print('CAPTURE_STATUS='+manifest['status'])
        if manifest['status'] == 'PARTIAL':
            print('CAPTURE_ERROR='+manifest['error']); return 2
    else:
        if args.output.exists(): p.error('output exists; preserve prior results')
        report = evaluate(args.input, json.loads(args.plan.read_text(encoding='utf-8')))
        with args.output.open('x', encoding='utf-8') as f:
            json.dump(report, f, indent=2)
        passed = sum(x['research_screen_pass'] for x in report['aggregates'])
        print(f'RESEARCH_CELLS_PASS={passed}/{len(report["aggregates"])}; PROMOTION=NONE')


if __name__ == '__main__':
    raise SystemExit(main())

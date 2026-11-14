"""Run with --help; reads caller-supplied CSV only, prints JSON to stdout."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path

from .candidates import Bar
from .replay import Costs, compare


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--timeframe", choices=("minute", "daily"), required=True)
    parser.add_argument("--split-at", type=datetime.fromisoformat, required=True)
    parser.add_argument("--adverse-bps-per-side", type=Decimal, required=True)
    parser.add_argument("--fee-per-share-per-side", type=Decimal, required=True)
    parser.add_argument("--max-hold-bars", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        raw = args.csv.read_bytes()
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        bars = tuple(Bar(
            row["symbol"], datetime.fromisoformat(row["opened"]),
            datetime.fromisoformat(row["closed"]),
            *(Decimal(row[k]) for k in ("open", "high", "low", "close", "volume")),
            datetime.fromisoformat(row["session_end"]),
        ) for row in reader)
        result = compare(bars, Costs(args.adverse_bps_per_side, args.fee_per_share_per_side),
                         timeframe=args.timeframe, split_at=args.split_at,
                         max_hold=args.max_hold_bars)
        result["input_sha256"] = sha256(raw).hexdigest()
        config = {"timeframe": args.timeframe, "split_at": args.split_at.isoformat(),
                  "costs": asdict(Costs(args.adverse_bps_per_side, args.fee_per_share_per_side)),
                  "max_hold_bars": args.max_hold_bars, "schema_version": 1}
        result["configuration"] = config
        result["configuration_sha256"] = sha256(json.dumps(config, default=str, sort_keys=True).encode()).hexdigest()
        result["implementation_sha256"] = sha256(b"".join(
            Path(__file__).with_name(name).read_bytes()
            for name in ("candidates.py", "replay.py", "__main__.py")
        )).hexdigest()
        print(json.dumps(result, default=str, sort_keys=True, indent=2, allow_nan=False))
    except (ValueError, KeyError, OSError, ArithmeticError) as exc:
        parser.exit(2, f"Screening failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

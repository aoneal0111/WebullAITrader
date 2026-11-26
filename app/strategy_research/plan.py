"""Print the frozen five-market plan or assess supplied research evidence."""
import argparse
from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path

from .multi_asset import PROFILES
from .review_gate import ReviewEvidence, review


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    result = {'scope': 'FIVE_MARKET_RESEARCH_PLAN', 'execution_authorized': False,
              'profiles': [asdict(p) for p in PROFILES],
              'status': 'AWAITING_MARKET_SPECIFIC_DATA',
              'readiness': {'EQUITY_MOMENTUM': 'OFFLINE_REPLAY_CANDIDATE',
                            'SCALPER': 'OFFLINE_REPLAY_CANDIDATE',
                            'CRYPTO': 'OFFLINE_SPOT_SIGNAL_PROTOTYPE',
                            'FUTURES': 'OFFLINE_SIGNAL_ONLY_DESKTOP_FEED_NOT_CONNECTED',
                            'OPTIONS': 'PAYOFF_MODEL_ONLY_MULTI_LEG_EXECUTION_UNSUPPORTED'}}
    try:
        if args.evidence:
            if args.evidence.stat().st_size > 16384:
                raise ValueError('Evidence file too large')
            values = json.loads(args.evidence.read_text(encoding='utf-8-sig'))
            for key in ('expectancy_r', 'stress_expectancy_r', 'profit_factor_r', 'drawdown_r'):
                if not isinstance(values[key], str):
                    raise ValueError('Decimal statistics must be strings')
                values[key] = Decimal(values[key])
            result['review'] = review(ReviewEvidence(**values))
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, TypeError, KeyError, OSError, ArithmeticError):
        parser.exit(2, 'Invalid research evidence. No execution authorization was granted.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

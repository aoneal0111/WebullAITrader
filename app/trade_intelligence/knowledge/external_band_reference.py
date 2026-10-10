"""Offline Concretum band/VWAP signal reference, with no orders or fill model.

Inputs are precomputed completed-minute features, not quotes. Their derivation
must be independently audited; timestamps cannot establish feature correctness.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path

from .capture_profit_audit import instant


def sampled_band_reference(rows, *, trade_frequency=30):
    """Return scheduled decisions and one-minute-lagged exposure.

One symbol/session only, contiguous rows from the first completed minute.
Sigma must describe prior sessions and VWAP must use only completed bars.
Missing features are unresolved, never treated as a neutral trading signal.
"""
    if (not isinstance(trade_frequency, int) or isinstance(trade_frequency, bool)
            or not 1 <= trade_frequency <= 390 or len(rows) > 390):
        raise ValueError("INVALID_BOUNDS")
    if not rows:
        return {"status": "NO_ROWS", "records": [], "candidate_pnl": None}
    output = []
    session_open = instant(rows[0]["session_open_at"])
    symbol = rows[0]["symbol"]
    if not symbol:
        raise ValueError("MISSING_SYMBOL")
    exposure = 0
    for minute, row in enumerate(rows, 1):
        completed = instant(row["completed_at"])
        if row["symbol"] != symbol or instant(row["session_open_at"]) != session_open:
            raise ValueError("MIXED_SYMBOL_OR_SESSION")
        if completed != session_open + timedelta(minutes=minute):
            return {"status": "UNRESOLVED_MISSING_OR_UNORDERED_MINUTES",
                    "records": output, "candidate_pnl": None}
        record = {"completed_at": completed.isoformat(), "minute_from_open": minute,
                  "exposure_for_interval_just_completed": exposure,
                  "scheduled": minute % trade_frequency == 0}
        if record["scheduled"]:
            fields = ("close", "vwap", "session_open_price", "previous_close_adjusted", "sigma_open")
            if any(row.get(key) is None for key in (*fields, "history_cutoff", "features_available_at")):
                return {"status": "UNRESOLVED_FEATURE_COVERAGE", "records": output,
                        "candidate_pnl": None, "unresolved_at": completed.isoformat()}
            if (instant(row["history_cutoff"]) >= session_open
                    or instant(row["features_available_at"]) > completed):
                raise ValueError("FUTURE_FEATURE_EVIDENCE")
            close, vwap, opening, previous_close, sigma = (Decimal(str(row[key])) for key in fields)
            if (any(not value.is_finite() for value in (close, vwap, opening, previous_close, sigma))
                    or min(close, vwap, opening, previous_close) <= 0 or not 0 <= sigma < 1):
                raise ValueError("INVALID_FEATURE_VALUE")
            upper = max(opening, previous_close) * (1 + sigma)
            lower = min(opening, previous_close) * (1 - sigma)
            target = 1 if close > max(upper, vwap) else -1 if close < min(lower, vwap) else 0
            record.update(upper_band=str(upper), lower_band=str(lower), target_exposure=target,
                          long_exit_observed=exposure == 1 and target != 1,
                          short_exit_observed=exposure == -1 and target != -1,
                          effective_after=completed.isoformat())
            # The completed interval retains the prior signal. This newly
            # observed decision can affect only the next interval, not its own.
            exposure = target
        output.append(record)
    return {"version": "CONCRETUM_SAMPLED_BAND_REFERENCE_V1", "status": "SIGNALS_ONLY",
            "symbol": symbol, "trade_frequency": trade_frequency, "band_multiplier": "1",
            "records": output, "candidate_pnl": None,
            "scope": "EXTERNAL_FEATURE_SIGNAL_REFERENCE_NOT_FULL_STRATEGY_OR_EXECUTION",
            "next_interval_exposure": exposure}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("features", type=Path)
    args = parser.parse_args()
    with args.features.open("rb") as handle:
        raw = handle.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("INPUT_BYTE_LIMIT")
    print(json.dumps(sampled_band_reference(json.loads(raw)["rows"]), indent=2))


if __name__ == "__main__":
    main()

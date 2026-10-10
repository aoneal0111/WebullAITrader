"""Offline exit-policy comparison on ordered bid/ask observations.

This is a quote-fill proxy for one long instrument, not broker execution or a
portfolio backtest. A long put requires its own option quotes, just like a call.
"""
import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Quote:
    observed_at: datetime
    source_at: datetime
    bid: Decimal
    ask: Decimal

    @classmethod
    def parse(cls, row):
        quote = cls(datetime.fromisoformat(row["observed_at"]),
                    datetime.fromisoformat(row["source_at"]),
                    Decimal(str(row["bid"])), Decimal(str(row["ask"])))
        if quote.observed_at.tzinfo is None or quote.source_at.tzinfo is None:
            raise ValueError("Quote timestamps must be timezone-aware")
        if not (quote.bid.is_finite() and quote.ask.is_finite()
                and Decimal("0") < quote.bid <= quote.ask):
            raise ValueError("Invalid bid/ask")
        return quote


def compare(rows, *, signal_at, stop, targets=(Decimal("0.05"), Decimal("0.10")),
            multiplier=Decimal("1"), risk_budget=Decimal("25"), capital_cap=Decimal("2500"),
            fee_per_unit_per_side=Decimal("0"), max_age_seconds=2, max_gap_seconds=5,
            hold_seconds=60, entry_valid_seconds=5):
    """Use first fresh quote strictly after a predeclared signal; never optimize it."""
    if signal_at.tzinfo is None:
        raise ValueError("Signal timestamp must be timezone-aware")
    if not targets or len(targets) > 20:
        raise ValueError("Provide between one and twenty targets")
    for value in (stop, multiplier, risk_budget, capital_cap, *targets):
        if not value.is_finite() or value <= 0:
            raise ValueError("Prices, budgets, multiplier and targets must be positive finite decimals")
    if not fee_per_unit_per_side.is_finite() or fee_per_unit_per_side < 0:
        raise ValueError("Fees must be finite and nonnegative")
    if min(max_age_seconds, max_gap_seconds, hold_seconds, entry_valid_seconds) <= 0:
        raise ValueError("Time limits must be positive")
    if len(rows) > 10000:
        raise ValueError("At most 10000 quotes per comparison")
    quotes = [Quote.parse(row) for row in rows]
    if any(b.observed_at <= a.observed_at for a, b in zip(quotes, quotes[1:])):
        raise ValueError("Observed timestamps must be strictly increasing")

    def fresh(q):
        age = (q.observed_at - q.source_at).total_seconds()
        return 0 <= age <= max_age_seconds

    entry_index = next((i for i, q in enumerate(quotes)
                        if 0 < (q.observed_at - signal_at).total_seconds() <= entry_valid_seconds
                        and fresh(q)), None)
    if entry_index is None:
        return {"status": "NO_FRESH_ENTRY", "results": []}
    entry = quotes[entry_index]
    if entry.ask <= stop:
        return {"status": "ENTRY_AT_OR_BELOW_STOP", "results": []}
    quantity = int(min(risk_budget / ((entry.ask - stop) * multiplier + 2 * fee_per_unit_per_side),
                       capital_cap / (entry.ask * multiplier + fee_per_unit_per_side)))
    if quantity < 1:
        return {"status": "NO_EXECUTABLE_SIZE", "results": []}
    results = []
    for target in targets:
        status, exit_quote, reason = "UNRESOLVED_END_OF_CAPTURE", None, None
        previous = entry
        for quote in quotes[entry_index + 1:]:
            # A gap or invalid quote invalidates this policy's remaining path.
            if (quote.observed_at - previous.observed_at).total_seconds() > max_gap_seconds:
                status = "UNRESOLVED_QUOTE_GAP"
                break
            if not fresh(quote):
                status = "UNRESOLVED_QUOTE_AGE"
                break
            elapsed = (quote.observed_at - entry.observed_at).total_seconds()
            reason = ("STOP" if quote.bid <= stop else
                      "TARGET" if quote.bid >= entry.ask + target else
                      "MAX_HOLD" if elapsed >= hold_seconds else None)
            if reason:
                status, exit_quote = "CLOSED_QUOTE_PROXY", quote
                break
            previous = quote
        pnl = None if exit_quote is None else (
            (exit_quote.bid - entry.ask) * multiplier - 2 * fee_per_unit_per_side) * quantity
        results.append({"target_price_increment": str(target), "status": status,
                        "exit_reason": reason, "net_pnl_proxy": None if pnl is None else str(pnl)})
    return {"status": "ENTRY_QUOTE_PROXY", "entry_at": entry.observed_at.isoformat(),
            "entry_ask": str(entry.ask), "quantity": quantity, "multiplier": str(multiplier),
            "results": results, "paired_closed": all(r["status"] == "CLOSED_QUOTE_PROXY" for r in results),
            "scope": "ONE_LONG_INSTRUMENT_NO_QUEUE_DEPTH_OR_PORTFOLIO_MODEL"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON with signal_at, stop and quotes for one instrument")
    parser.add_argument("--multiplier", type=Decimal, default=Decimal("1"))
    parser.add_argument("--fee-per-unit-per-side", type=Decimal, default=Decimal("0"))
    args = parser.parse_args()
    if args.input.stat().st_size > 2_000_000:
        parser.error("Input exceeds 2 MB")
    data = json.loads(args.input.read_text(encoding="utf-8"))
    print(json.dumps(compare(data["quotes"], signal_at=datetime.fromisoformat(data["signal_at"]),
                             stop=Decimal(str(data["stop"])), multiplier=args.multiplier,
                             fee_per_unit_per_side=args.fee_per_unit_per_side), indent=2))


if __name__ == "__main__":
    main()

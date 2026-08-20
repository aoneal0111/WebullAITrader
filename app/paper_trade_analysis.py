"""Read-only summary command for Atlas paper trade experiments."""

from __future__ import annotations

import argparse
import json
import statistics
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from app.paper_trade_experiment import COHORTS, CandidateRecord
from app.paper_trade_experiment.journal import read_records


def build_report(records: Iterable[CandidateRecord]) -> dict[str, Any]:
    values = tuple(records)
    actual = tuple(
        item for item in values
        if item.execution.get("state") == "CLOSED"
        and item.execution.get("paper_trade_executed") is True
    )
    report = {
        "actual_paper_trades": _actual_summary(actual),
        "cohorts": {
            cohort: _outcome_summary(
                item for item in values
                if cohort in item.features.get("cohort_flags", [])
            )
            for cohort in COHORTS
        },
        "by_catalyst_source": _breakdown(values, "selected_source"),
        "by_catalyst_type": _breakdown(values, "catalyst_type"),
        "disclaimer": "Descriptive results only; no statistical significance is claimed.",
    }
    return report


def _actual_summary(records: tuple[CandidateRecord, ...]) -> dict[str, Any]:
    returns = tuple(
        Decimal(item.execution["return_percent"]) / Decimal("100")
        for item in records
    )
    pnls = tuple(Decimal(item.execution["realized_pnl"]) for item in records)
    mfes = tuple(
        Decimal(item.execution.get("actual_mfe", item.labels.get("mfe", "0")))
        for item in records
    )
    maes = tuple(
        Decimal(item.execution.get("actual_mae", item.labels.get("mae", "0")))
        for item in records
    )
    return {
        "count": len(records),
        "win_rate": _ratio(sum(value > 0 for value in pnls), len(pnls)),
        "total_pnl": _sum(pnls),
        "average_return": _mean(returns),
        "median_return": _median(returns),
        "expectancy": _mean(pnls),
        "average_mfe": _mean(mfes),
        "average_mae": _mean(maes),
    }


def _outcome_summary(records: Iterable[CandidateRecord]) -> dict[str, Any]:
    all_records = tuple(records)
    labeled = tuple(
        item for item in all_records if "return_after_30m" in item.labels
    )
    returns = tuple(Decimal(item.labels["return_after_30m"]) for item in labeled)
    return {
        "sample_count": len(labeled),
        "pending_count": len(all_records) - len(labeled),
        "positive_outcome_rate": _ratio(sum(value > 0 for value in returns), len(returns)),
        "mean_return": _mean(returns),
        "median_return": _median(returns),
        "mean_mfe": _mean(_label_values(labeled, "mfe")),
        "mean_mae": _mean(_label_values(labeled, "mae")),
    }


def _breakdown(records: tuple[CandidateRecord, ...], feature: str) -> dict[str, Any]:
    keys = sorted({str(item.features.get(feature) or "NO_CATALYST") for item in records})
    return {
        key: _outcome_summary(
            item for item in records
            if str(item.features.get(feature) or "NO_CATALYST") == key
        )
        for key in keys
    }


def _label_values(records: Iterable[CandidateRecord], key: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(item.labels[key]) for item in records if key in item.labels)


def _ratio(numerator: int, denominator: int) -> str | None:
    return None if denominator == 0 else str(Decimal(numerator) / Decimal(denominator))


def _sum(values: tuple[Decimal, ...]) -> str:
    return str(sum(values, Decimal("0")))


def _mean(values: tuple[Decimal, ...]) -> str | None:
    return None if not values else str(sum(values, Decimal("0")) / Decimal(len(values)))


def _median(values: tuple[Decimal, ...]) -> str | None:
    return None if not values else str(statistics.median(values))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--journal", type=Path,
        default=Path("data/paper_trade_experiment.sqlite3"),
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    report = build_report(read_records(args.journal))
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_report(report)
    return 0


def _print_report(report: dict[str, Any]) -> None:
    print("Actual paper trades")
    for key, value in report["actual_paper_trades"].items():
        print(f"  {key}: {value}")
    print("Cohorts")
    for cohort, summary in report["cohorts"].items():
        print(f"  {cohort}")
        for key, value in summary.items():
            print(f"    {key}: {value}")
    for title, section in (
        ("Catalyst sources", report["by_catalyst_source"]),
        ("Catalyst types", report["by_catalyst_type"]),
    ):
        print(title)
        for name, summary in section.items():
            print(f"  {name}: {json.dumps(summary, sort_keys=True)}")
    print(report["disclaimer"])


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_report", "main"]

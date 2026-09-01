"""Deterministic read-only reporting over autonomous experiences."""

from __future__ import annotations

from decimal import Decimal
from statistics import median

from .experience_store import ExperienceStore


class ExperienceReporter:
    def __init__(self, store: ExperienceStore) -> None:
        self.store = store

    def summary(self) -> dict[str, object]:
        data = self.store.aggregate_report()
        cohorts = data.pop("blocker_cohort_rows")
        data["blocker_cohorts"] = {
            blocker: _cohort_summary(rows) for blocker, rows in cohorts.items()
        }
        return data

    def _blocker_cohorts(self, experiences, by_exp):
        result = {}
        blockers = sorted({blocker for item in experiences for blocker in item.blockers})
        for blocker in blockers:
            cohort = [item for item in experiences if blocker in item.blockers]
            longest = []
            for exp in cohort:
                complete = [item for item in by_exp[exp.experience_id] if item.status is OutcomeStatus.COMPLETE]
                if complete:
                    longest.append(max(complete, key=lambda item: item.horizon_minutes))
            result[blocker] = {
                "sample_size": len(longest),
                "reached_1r_rate": _rate(longest, "reached_1r"),
                "reached_2r_rate": _rate(longest, "reached_2r"),
                "stop_first_rate": _event_rate(longest, "STOP"),
                "median_mfe_r": _median(longest, "mfe_r"),
                "median_mae_r": _median(longest, "mae_r"),
            }
        return result


def _count(values, key):
    return dict(sorted(Counter(key(item) for item in values).items()))


def _rate(values, field):
    known = [getattr(item, field) for item in values if getattr(item, field) is not None]
    return None if not known else Decimal(sum(bool(value) for value in known)) / Decimal(len(known))


def _event_rate(values, event):
    known = [item.first_plan_event for item in values if item.first_plan_event is not None]
    return None if not known else Decimal(sum(value == event for value in known)) / Decimal(len(known))


def _median(values, field):
    known = [getattr(item, field) for item in values if getattr(item, field) is not None]
    return None if not known else median(known)


def _cohort_summary(rows):
    complete = [row for row in rows if row[0] is not None]
    mfe = [Decimal(row[3]) for row in complete if row[3] is not None]
    mae = [Decimal(row[4]) for row in complete if row[4] is not None]
    return {
        "sample_size": len(complete),
        "reached_1r_rate": None if not complete else Decimal(sum(bool(row[0]) for row in complete)) / Decimal(len(complete)),
        "reached_2r_rate": None if not complete else Decimal(sum(bool(row[1]) for row in complete)) / Decimal(len(complete)),
        "stop_first_rate": None if not complete else Decimal(sum(bool(row[2]) for row in complete)) / Decimal(len(complete)),
        "median_mfe_r": None if not mfe else median(mfe),
        "median_mae_r": None if not mae else median(mae),
    }

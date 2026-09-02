"""Snapshot-only command line census for offline research evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cohorts import analyze_blockers, analyze_feature_cohorts
from .contracts import LearningTarget, ResearchEvidencePolicy
from .dataset import assess_sufficiency, build_learning_examples
from .reporting import PULLBACK_DIMENSIONS
from .snapshot import ImmutableSnapshotReader, merge_snapshot_readers


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Analyze external Trade Intelligence snapshots only")
    parser.add_argument("snapshots", nargs="+", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--census-only", action="store_true")
    args = parser.parse_args(argv)
    readers = tuple(ImmutableSnapshotReader(path) for path in args.snapshots)
    integrity = tuple(reader.integrity_check() for reader in readers)
    experiences, outcomes, decisions, paper = merge_snapshot_readers(readers)
    examples = build_learning_examples(experiences, outcomes, decisions)
    by_experience = {}
    for example in examples:
        by_experience.setdefault(example.features.experience_id, example.labels)
        if example.labels is not None:
            by_experience[example.features.experience_id] = example.labels
    eligible = [value for value in by_experience.values() if value is not None]
    policy = ResearchEvidencePolicy()
    blockers = analyze_blockers(examples, policy)
    pullbacks = analyze_feature_cohorts(examples, PULLBACK_DIMENSIONS, policy)
    result = {
        "integrity": integrity,
        "experiences": len(experiences),
        "decision_examples": len(examples),
        "decisions": len(decisions),
        "outcomes": len(outcomes),
        "paper_observations": len(paper),
        "unique_dates": len({item.key.session_date for item in experiences}),
        "unique_symbols": len({item.key.symbol for item in experiences}),
        "unique_sessions": len({item.key.session for item in experiences}),
        "experience_partitions": {
            name: sum(item.partition.value == name for item in experiences)
            for name in ("TRAIN", "VALIDATION", "HOLDOUT")
        },
        "eligible_experiences": len(eligible),
        "complete_1r_labels": len(eligible),
        "complete_2r_labels": len(eligible),
        "complete_3r_labels": len(eligible),
        "one_r_positive": sum(item.one_r_before_stop for item in eligible),
        "two_r_positive": sum(item.two_r_before_stop for item in eligible),
        "three_r_positive": sum(item.three_r_before_stop for item in eligible),
        "stop_first_positive": sum(item.stop_before_one_r for item in eligible),
        "sufficiency": {
            target.value: {
                "status": assessment.status.value,
                "reasons": assessment.reasons,
            }
            for target in LearningTarget
            for assessment in (assess_sufficiency(examples, target, policy),)
        },
        "blocker_cohorts": [] if args.census_only else [
            {"blocker": item.blocker, "context": item.context,
             "sample": item.statistics.sample_size, "one_r": item.statistics.one_r_rate,
             "two_r": item.statistics.two_r_rate, "three_r": item.statistics.three_r_rate,
             "stop_first": item.statistics.stop_first_rate,
             "evidence": item.statistics.evidence_status.value,
             "tied": item.tied_blockers}
            for item in blockers
        ],
        "pullback_cohorts": [] if args.summary_only else [
            {"cohort": item.cohort, "sample": item.sample_size,
             "one_r": item.one_r_rate, "two_r": item.two_r_rate,
             "three_r": item.three_r_rate, "stop_first": item.stop_first_rate,
             "evidence": item.evidence_status.value}
            for item in pullbacks
        ],
        "single_dimension_findings": {
            feature: [
                {"cohort": item.cohort, "sample": item.sample_size,
                 "one_r": item.one_r_rate, "two_r": item.two_r_rate,
                 "three_r": item.three_r_rate, "stop_first": item.stop_first_rate,
                 "evidence": item.evidence_status.value}
                for item in analyze_feature_cohorts(examples, (feature,), policy)
                if item.sample_size >= 5
            ]
            for feature in (
                "setup_type", "catalyst_status", "spread_percent", "relative_volume",
                "float_shares", "pullback_depth_percent", "higher_low",
                "pullback_volume_contraction_ratio",
                "recent_momentum_velocity_percent_per_minute", "distance_from_hod_percent",
            )
        } if args.summary_only and not args.census_only else {},
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

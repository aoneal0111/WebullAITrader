"""Comprehensive deterministic Phase 2A research reporting."""

from __future__ import annotations

from dataclasses import asdict

from .cohorts import analyze_blockers, analyze_feature_cohorts
from .contracts import LearningTarget, ResearchEvidencePolicy
from .dataset import assess_sufficiency, latest_by_experience
from .evaluation import compare_champion, evaluate_challenger

PULLBACK_DIMENSIONS = (
    "setup_type", "pullback_depth_percent", "higher_low",
    "pullback_volume_contraction_ratio", "recent_momentum_velocity_percent_per_minute",
    "distance_from_hod_percent", "spread_percent", "relative_volume",
    "float_shares", "catalyst_status",
)


def build_research_report(examples, challengers, policy: ResearchEvidencePolicy = ResearchEvidencePolicy(),
                          *, selected_challenger_ids: tuple[str, ...] = ()):
    examples = tuple(examples)
    unique = latest_by_experience(examples)
    labeled = [item for item in unique if item.labels is not None]
    dataset = {
        "total_decision_examples": len(examples),
        "unique_experiences": len({item.features.experience_id for item in examples}),
        "complete_labels": len(labeled),
        "partitions": {name: sum(item.partition.value == name for item in examples) for name in ("TRAIN", "VALIDATION", "HOLDOUT")},
        "dates": sorted({item.features.session_date.isoformat() for item in examples}),
        "symbols": sorted({item.features.symbol for item in examples}),
        "sessions": sorted({item.features.session for item in examples}),
        "setup_types": sorted({str(item.features.as_mapping().get("setup_type")) for item in examples}),
    }
    champion = {
        "opportunities_considered": len(unique),
        "opportunities_entered": sum(item.champion_selected for item in unique),
        "missed_opportunities": sum(not item.champion_selected and item.labels is not None and item.labels.two_r_before_stop for item in unique),
        "protected_rejections": sum(not item.champion_selected and item.labels is not None and item.labels.stop_before_one_r for item in unique),
    }
    challenger_reports = {}
    for challenger in challengers:
        partitions = {}
        for name in ("TRAIN", "VALIDATION", "HOLDOUT"):
            rows = tuple(item for item in examples if item.partition.value == name)
            partitions[name] = (
                {"status": "UNTOUCHED_UNTIL_SELECTION"}
                if name == "HOLDOUT" and challenger.challenger_id not in selected_challenger_ids
                else asdict(evaluate_challenger(challenger, rows))
            )
        challenger_reports[challenger.challenger_id] = {
            "target": challenger.target.value,
            "partitions": partitions,
            "champion_comparison": asdict(compare_champion(challenger, examples)),
        }
    return {
        "dataset": dataset,
        "champion": champion,
        "sufficiency": {target.value: asdict(assess_sufficiency(examples, target, policy)) for target in LearningTarget},
        "blockers": [asdict(value) for value in analyze_blockers(examples, policy)],
        "pullbacks_and_momentum": [asdict(value) for value in analyze_feature_cohorts(examples, PULLBACK_DIMENSIONS, policy)],
        "catalyst": [asdict(value) for value in analyze_feature_cohorts(examples, ("catalyst_status", "catalyst_type"), policy)],
        "spread": [asdict(value) for value in analyze_feature_cohorts(examples, ("spread_percent",), policy)],
        "rvol_float": [asdict(value) for value in analyze_feature_cohorts(examples, ("relative_volume", "float_shares"), policy)],
        "setup_type": [asdict(value) for value in analyze_feature_cohorts(examples, ("setup_type",), policy)],
        "challengers": challenger_reports,
        "disclaimer": "RESEARCH ONLY: no recommendation has order, broker, account, sizing, veto, or execution authority.",
    }

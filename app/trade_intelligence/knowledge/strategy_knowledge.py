"""Taxonomy-grounded strategy reference records, separate from market episodes."""

from __future__ import annotations

from app.opportunity_discovery.taxonomy import STRATEGY_TAXONOMY


def catalog() -> tuple[dict[str, object], ...]:
    return tuple({
        "strategy_id": item.strategy_id, "family": item.family.value,
        "definition": item.description, "required_features": item.required_features,
        "source_type": "ATLAS_TAXONOMY", "source": "repository taxonomy.py",
        "educational_status": "structured internal reference, not historical evidence",
    } for item in STRATEGY_TAXONOMY if item.availability.value == "ACTIVE")

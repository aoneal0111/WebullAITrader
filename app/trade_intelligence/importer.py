"""Deterministic importer boundary for user-provided external snapshot copies.

This module never locates or opens authoritative stores. A caller must supply a
fresh external snapshot fixture and a pure row decoder. Imports write only to
the Trade Intelligence store and carry complete provenance for deduplication.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .experience_store import ExperienceStore
from .models import ExperienceSource, TradeOpportunityExperience


def import_external_snapshot_rows(
    destination: ExperienceStore,
    snapshot_path: str | Path,
    rows: Iterable[Mapping[str, object]],
    decoder: Callable[[Mapping[str, object]], TradeOpportunityExperience],
    *,
    source_schema_version: str,
    import_version: str,
) -> tuple[int, int]:
    path = Path(snapshot_path).resolve()
    if not path.exists() or not source_schema_version or not import_version:
        raise ValueError("an existing external snapshot and version provenance are required")
    inserted = duplicate = 0
    for row in rows:
        identity = str(row.get("source_record_identity", "")).strip()
        if not identity:
            raise ValueError("each imported row requires source_record_identity")
        value = replace(
            decoder(row), source=ExperienceSource.EXTERNAL_SNAPSHOT_IMPORT,
            source_store=str(path), source_schema_version=source_schema_version,
            import_version=import_version, source_record_identity=identity,
        )
        if destination.put_experience(value):
            inserted += 1
        else:
            duplicate += 1
    return inserted, duplicate

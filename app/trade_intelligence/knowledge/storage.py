"""Append-only JSONL storage for local research data.

JSONL keeps the schema inspectable and avoids putting generated data in Git.
Indexes are rebuilt once at open and bounded per build process; no global
O(N²) comparison is performed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from .models import KnowledgeEpisode, QuarantineRecord


class KnowledgeStore:
    def __init__(self, root: Path, *, create: bool = True) -> None:
        self.root = Path(root)
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        self.episodes_path = self.root / "episodes.jsonl"
        self.memberships_path = self.root / "memberships.jsonl"
        self.outcomes_path = self.root / "outcomes.jsonl"
        self.quarantine_path = self.root / "quarantine.jsonl"
        self.manifest_path = self.root / "manifest.json"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.mined_partitions_path = self.root / "mined_partitions.jsonl"
        self._ids = set()
        self._near = set()
        self._quarantine_ids = set()
        self._mined_records: dict[str, dict[str, object]] = {}
        self._complete_by_content_key: dict[str, dict[str, object]] = {}
        if self.episodes_path.exists():
            for row in self._read(self.episodes_path):
                self._ids.add(row["episode_id"])
                for member in row.get("strategy_memberships", ()):
                    self._near.add("|".join((row["symbol"], row["trading_date"], row["session"], member, row["opportunity_anchor"])))
        if self.quarantine_path.exists():
            for row in self._read(self.quarantine_path):
                self._quarantine_ids.add(row.get("record_id"))
        self._load_mining_index()

    def _load_mining_index(self) -> None:
        """Load the mining journal once; hot-path lookups use these maps."""
        if self.mined_partitions_path.exists():
            for row in self._read(self.mined_partitions_path):
                identity = row.get("mining_partition_id")
                if identity:
                    self._mined_records[str(identity)] = row
        self._rebuild_mining_indexes()

    def _rebuild_mining_indexes(self) -> None:
        self._complete_by_content_key = {}
        for record in self._mined_records.values():
            if record.get("status") != "COMPLETE":
                continue
            key = str(record.get("mining_content_key") or self.compatibility_key(record) or record.get("mining_partition_id"))
            self._complete_by_content_key.setdefault(key, record)

    @staticmethod
    def _read(path: Path):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    @property
    def episode_ids(self) -> frozenset[str]:
        return frozenset(self._ids)

    def has_episode(self, episode_id: str) -> bool:
        return episode_id in self._ids

    def has_near(self, key: str) -> bool:
        return key in self._near

    def append_episode(self, episode: KnowledgeEpisode) -> None:
        if episode.episode_id in self._ids:
            raise ValueError("EXACT_DUPLICATE")
        with self.episodes_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(episode.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
        with self.memberships_path.open("a", encoding="utf-8", newline="\n") as handle:
            for strategy in episode.strategy_memberships:
                handle.write(json.dumps({"episode_id": episode.episode_id, "strategy": strategy}, sort_keys=True) + "\n")
        with self.outcomes_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"episode_id": episode.episode_id, "outcomes": episode.outcomes}, sort_keys=True) + "\n")
        self._ids.add(episode.episode_id)
        for strategy in episode.strategy_memberships:
            self._near.add("|".join((episode.symbol, episode.trading_date.isoformat(), episode.session, strategy, episode.opportunity_anchor)))

    def append_quarantine(self, record: QuarantineRecord) -> None:
        if record.record_id in self._quarantine_ids:
            return
        with self.quarantine_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
        self._quarantine_ids.add(record.record_id)

    def write_manifest(self, values: dict[str, object]) -> None:
        self.manifest_path.write_text(json.dumps(values, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    def write_checkpoint(self, values: dict[str, object]) -> None:
        temp = self.checkpoint_path.with_name(self.checkpoint_path.name + ".tmp")
        temp.write_text(json.dumps(values, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        os.replace(temp, self.checkpoint_path)

    def mined_partitions(self, *, refresh: bool = True) -> dict[str, dict[str, object]]:
        # The mapping is session-owned and intentionally kept in memory.  A
        # caller must treat it as read-only.  The default refresh keeps the
        # public diagnostic API accurate across independently opened stores;
        # mining sessions use refresh=False for O(1) lookups.
        if refresh and self.mined_partitions_path.exists():
            self._mined_records = {}
            self._load_mining_index()
        return self._mined_records

    def record_mined_partition(self, record: dict[str, object]) -> None:
        identity = str(record["mining_partition_id"])
        value = dict(record)
        existing = self._mined_records.get(identity)
        # A late/replayed IN_PROGRESS transition must never mask a durable
        # COMPLETE result for the same identity.  This is also what makes an
        # interrupted second process safe when another process already
        # finished the partition.
        if existing and existing.get("status") == "COMPLETE" and value.get("status") == "IN_PROGRESS":
            return
        with self.mined_partitions_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str) + "\n")
        self._mined_records[identity] = value
        key = str(value.get("mining_content_key") or self.compatibility_key(value) or identity)
        if value.get("status") == "COMPLETE":
            self._complete_by_content_key.setdefault(key, value)
        elif value.get("status") == "SUPERSEDED" and self._complete_by_content_key.get(key) is value:
            self._complete_by_content_key.pop(key, None)

    @staticmethod
    def compatibility_key(record: dict[str, object]) -> str | None:
        fields = ("candidate_plan_id", "symbol", "trading_date", "normalized_sha256",
                  "knowledge_schema_version", "feature_derivation_version",
                  "strategy_semantics_version", "mining_semantics_version")
        if not all(record.get(field) is not None for field in fields[:4]):
            return None
        values = (str(record.get(field) or {"knowledge_schema_version": 1,
                    "feature_derivation_version": "ATLAS_PIT_FEATURES_V1",
                    "strategy_semantics_version": "ATLAS_STRATEGY_SEMANTICS_V1",
                    "mining_semantics_version": "ATLAS_MINING_SEMANTICS_V1"}[field]) for field in fields)
        import hashlib
        return hashlib.sha256("|".join(("ATLAS_MINING_IDENTITY_V2", *values)).encode()).hexdigest()

    def effective_mined_partitions(self) -> dict[str, dict[str, object]]:
        return dict(self._complete_by_content_key)

    def compatible_complete(self, *, candidate_plan_id: str, symbol: str,
                            trading_date: str, normalized_sha256: str,
                            knowledge_schema_version: int = 1,
                            feature_derivation_version: str = "ATLAS_PIT_FEATURES_V1",
                            strategy_semantics_version: str = "ATLAS_STRATEGY_SEMANTICS_V1",
                            mining_semantics_version: str = "ATLAS_MINING_SEMANTICS_V1") -> dict[str, object] | None:
        wanted = {"candidate_plan_id": candidate_plan_id, "symbol": symbol, "trading_date": trading_date,
                  "normalized_sha256": normalized_sha256, "knowledge_schema_version": knowledge_schema_version,
                  "feature_derivation_version": feature_derivation_version,
                  "strategy_semantics_version": strategy_semantics_version,
                  "mining_semantics_version": mining_semantics_version}
        key = self.compatibility_key(wanted)
        return self._complete_by_content_key.get(key) if key else None

    def reconcile_mined_partitions(self) -> dict[str, int]:
        records = self._mined_records
        original_status = {identity: row.get("status") for identity, row in records.items()}
        groups: dict[str, list[dict[str, object]]] = {}
        for record in records.values():
            key = self.compatibility_key(record)
            if key:
                record.setdefault("mining_content_key", key)
                groups.setdefault(key, []).append(record)
        superseded = 0
        for group in groups.values():
            complete = next((row for row in group if row.get("status") == "COMPLETE"), None)
            if complete is None:
                continue
            winner = str(complete["mining_partition_id"])
            for row in group:
                if str(row["mining_partition_id"]) != winner and row.get("status") != "SUPERSEDED":
                    row.update({"status": "SUPERSEDED", "superseded_by": winner})
                    superseded += 1
        updates = [(identity, row) for identity, row in records.items()
                   if row.get("status") == "SUPERSEDED" and row.get("superseded_by")
                   and original_status.get(identity) != "SUPERSEDED"]
        for identity, row in updates:
            # Reconciliation is startup-only.  Append transitions instead of
            # rewriting an ever-growing ledger.
            with self.mined_partitions_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")
        self._rebuild_mining_indexes()
        return {"legacy_complete": sum(1 for row in records.values() if row.get("status") == "COMPLETE"),
                "effective_complete": len(self.effective_mined_partitions()), "superseded": superseded}

    def iter_episodes(self) -> Iterable[dict[str, object]]:
        return self._read(self.episodes_path) if self.episodes_path.exists() else iter(())

    def status(self) -> dict[str, object]:
        counts = {strategy: 0 for strategy in self._strategy_list()}
        unique_symbols = set()
        unique_dates = set()
        for row in self.iter_episodes():
            unique_symbols.add(row["symbol"])
            unique_dates.add(row["trading_date"])
            for strategy in row.get("strategy_memberships", ()):
                counts[strategy] = counts.get(strategy, 0) + 1
        return {"unique_episodes": len(self._ids), "strategy_memberships": sum(counts.values()),
                "unique_symbols": len(unique_symbols), "unique_dates": len(unique_dates),
                "per_strategy": counts}

    @staticmethod
    def _strategy_list() -> tuple[str, ...]:
        from .models import ACTIVE_STRATEGIES
        return ACTIVE_STRATEGIES

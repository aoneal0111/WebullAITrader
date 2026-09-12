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
        if self.episodes_path.exists():
            for row in self._read(self.episodes_path):
                self._ids.add(row["episode_id"])
                for member in row.get("strategy_memberships", ()):
                    self._near.add("|".join((row["symbol"], row["trading_date"], row["session"], member, row["opportunity_anchor"])))
        if self.quarantine_path.exists():
            for row in self._read(self.quarantine_path):
                self._quarantine_ids.add(row.get("record_id"))

    @staticmethod
    def _read(path: Path):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    @property
    def episode_ids(self) -> frozenset[str]:
        return frozenset(self._ids)

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

    def mined_partitions(self) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        if not self.mined_partitions_path.exists():
            return result
        for line in self._read(self.mined_partitions_path):
            identity = line.get("mining_partition_id")
            if identity:
                result[str(identity)] = line
        return result

    def record_mined_partition(self, record: dict[str, object]) -> None:
        identity = str(record["mining_partition_id"])
        records = self.mined_partitions()
        records[identity] = dict(record)
        temp = self.mined_partitions_path.with_name(self.mined_partitions_path.name + ".tmp")
        temp.write_text("".join(json.dumps(item, sort_keys=True, separators=(",", ":"), default=str) + "\n"
                                   for item in records.values()), encoding="utf-8")
        os.replace(temp, self.mined_partitions_path)

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
        effective: dict[str, dict[str, object]] = {}
        for record in self.mined_partitions().values():
            if record.get("status") != "COMPLETE":
                continue
            key = str(record.get("mining_content_key") or self.compatibility_key(record) or record.get("mining_partition_id"))
            effective.setdefault(key, record)
        return effective

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
        for record in self.mined_partitions().values():
            if record.get("status") != "COMPLETE":
                continue
            if all(str(record.get(field, default)) == str(default) for field, default in wanted.items()):
                return record
        return None

    def reconcile_mined_partitions(self) -> dict[str, int]:
        records = self.mined_partitions()
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
        if superseded or any("mining_content_key" not in row for row in records.values() if self.compatibility_key(row)):
            temp = self.mined_partitions_path.with_name(self.mined_partitions_path.name + ".tmp")
            temp.write_text("".join(json.dumps(item, sort_keys=True, separators=(",", ":"), default=str) + "\n"
                                       for item in records.values()), encoding="utf-8")
            os.replace(temp, self.mined_partitions_path)
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

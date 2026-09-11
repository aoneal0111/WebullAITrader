"""Append-only JSONL storage for local research data.

JSONL keeps the schema inspectable and avoids putting generated data in Git.
Indexes are rebuilt once at open and bounded per build process; no global
O(N²) comparison is performed.
"""

from __future__ import annotations

import json
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
        self.checkpoint_path.write_text(json.dumps(values, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

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

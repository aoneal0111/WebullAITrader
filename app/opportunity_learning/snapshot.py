"""Filesystem snapshot and read-only dataset access for offline research."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import shutil
import sqlite3

from app.trade_intelligence.experience_store import (
    _decision_from_json, _experience_from_json, _outcome_from_json,
    _paper_observation_from_json,
)
from app.trade_intelligence.models import canonical_json


@dataclass(frozen=True, slots=True)
class FileIdentity:
    path: str
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExternalSnapshot:
    source: tuple[FileIdentity, ...]
    copies: tuple[FileIdentity, ...]
    main_copy: Path


def file_identity(path: Path) -> FileIdentity:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    digest = sha256()
    with resolved.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return FileIdentity(str(resolved), stat.st_size, stat.st_mtime_ns, digest.hexdigest().upper())


def create_external_snapshot(source_main: Path, destination: Path) -> ExternalSnapshot:
    """Copy a complete SQLite family and abort if any source member changes."""

    source_main = source_main.resolve(strict=True)
    destination = destination.resolve()
    if source_main.parent == destination or destination == source_main.parent:
        raise ValueError("snapshot destination must be external to the source family")
    members = tuple(path for path in (source_main, Path(str(source_main) + "-wal"), Path(str(source_main) + "-shm")) if path.exists())
    before = tuple(file_identity(path) for path in members)
    destination.mkdir(parents=True, exist_ok=False)
    copied_paths = []
    for member in members:
        target = destination / member.name
        shutil.copy2(member, target)
        copied_paths.append(target)
    after = tuple(file_identity(path) for path in members)
    if before != after:
        raise RuntimeError("authoritative SQLite family changed during snapshot copy")
    copies = tuple(file_identity(path) for path in copied_paths)
    if tuple((item.size, item.sha256) for item in before) != tuple((item.size, item.sha256) for item in copies):
        raise RuntimeError("external SQLite snapshot copy verification failed")
    return ExternalSnapshot(before, copies, destination / source_main.name)


class ImmutableSnapshotReader:
    """Reads only an external copy via SQLite read-only/query-only mode."""

    def __init__(self, snapshot_main: Path, *, authoritative_paths: tuple[Path, ...] = ()) -> None:
        self.path = snapshot_main.resolve(strict=True)
        protected = {path.resolve(strict=True) for path in authoritative_paths}
        if self.path in protected:
            raise ValueError("authoritative runtime SQLite databases may not be opened")

    def integrity_check(self) -> str:
        with self._connect() as db:
            return str(db.execute("PRAGMA integrity_check").fetchone()[0])

    def experiences(self):
        return tuple(_experience_from_json(row[0]) for row in self._rows(
            "SELECT payload_json FROM experiences ORDER BY decision_timestamp,experience_id"
        ))

    def outcomes(self):
        return tuple(_outcome_from_json(row[0]) for row in self._rows(
            "SELECT payload_json FROM outcomes ORDER BY experience_id,horizon_minutes"
        ))

    def decisions(self):
        return tuple(_decision_from_json(row[0]) for row in self._rows_if_table(
            "experience_decisions", "SELECT payload_json FROM experience_decisions ORDER BY observed_at,decision_id"
        ))

    def paper_observations(self):
        return tuple(_paper_observation_from_json(row[0]) for row in self._rows_if_table(
            "paper_execution_observations", "SELECT payload_json FROM paper_execution_observations ORDER BY observed_at,observation_id"
        ))

    def _rows(self, sql):
        with self._connect() as db:
            return db.execute(sql).fetchall()

    def _rows_if_table(self, table, sql):
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            return () if exists is None else db.execute(sql).fetchall()

    def _connect(self):
        db = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        db.execute("PRAGMA query_only=ON")
        return db


def merge_snapshot_readers(readers: tuple[ImmutableSnapshotReader, ...]):
    """Deterministically deduplicate immutable facts from multiple snapshots."""

    experiences = _merge(readers, "experiences", lambda item: item.experience_id)
    outcomes = _merge(readers, "outcomes", lambda item: (item.experience_id, item.horizon_minutes))
    decisions = _merge(readers, "decisions", lambda item: item.decision_id)
    paper = _merge(readers, "paper_observations", lambda item: item.observation_id)
    return experiences, outcomes, decisions, paper


def _merge(readers, method, identity):
    merged = {}
    payloads = {}
    for reader in readers:
        for item in getattr(reader, method)():
            key = identity(item)
            payload = canonical_json(asdict(item))
            if key in payloads and payloads[key] != payload:
                raise ValueError(f"conflicting immutable snapshot identity: {key}")
            payloads[key] = payload
            merged[key] = item
    return tuple(merged[key] for key in sorted(merged, key=str))

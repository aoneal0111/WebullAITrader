"""Restart-safe SQLite/WAL store for immutable forward capture records."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from .forward_models import CAPTURE_SCHEMA_VERSION, CaptureRecord, CaptureRecordType


class CaptureSchemaError(RuntimeError):
    pass


class ForwardCaptureStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS capture_metadata (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS capture_records (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL,
                    record_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS capture_symbol_time
                    ON capture_records(symbol, timestamp, sequence);
                CREATE INDEX IF NOT EXISTS capture_type_time
                    ON capture_records(record_type, timestamp, sequence);
            """)
            row = connection.execute(
                "SELECT schema_version FROM capture_metadata WHERE singleton=1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO capture_metadata(singleton,schema_version) VALUES(1,?)",
                    (CAPTURE_SCHEMA_VERSION,),
                )
            elif row[0] != CAPTURE_SCHEMA_VERSION:
                raise CaptureSchemaError(
                    f"capture schema {row[0]} != {CAPTURE_SCHEMA_VERSION}"
                )

    def append_batch(self, records: tuple[CaptureRecord, ...]) -> tuple[int, int]:
        if not records:
            return 0, 0
        inserted = duplicates = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for record in records:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO capture_records
                    (record_id,schema_version,record_type,symbol,timestamp,payload_json)
                    VALUES(?,?,?,?,?,?)""",
                    (record.record_id, record.schema_version, record.record_type.value,
                     record.symbol, record.timestamp.isoformat(), record.payload_json),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    duplicates += 1
        return inserted, duplicates

    def records(
        self, *, symbol: str | None = None,
        record_type: CaptureRecordType | None = None,
    ) -> tuple[CaptureRecord, ...]:
        clauses: list[str] = []
        values: list[str] = []
        if symbol is not None:
            clauses.append("symbol=?")
            values.append(symbol.strip().upper())
        if record_type is not None:
            clauses.append("record_type=?")
            values.append(record_type.value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT schema_version,record_id,record_type,symbol,timestamp,payload_json "
                f"FROM capture_records{where} ORDER BY sequence", values,
            ).fetchall()
        return tuple(CaptureRecord(
            row[0], row[1], CaptureRecordType(row[2]), row[3],
            datetime.fromisoformat(row[4]), row[5],
        ) for row in rows)

    def count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM capture_records").fetchone()[0])

    def integrity_check(self) -> str:
        with self._connect() as connection:
            result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            for payload, in connection.execute("SELECT payload_json FROM capture_records"):
                import json
                if not isinstance(json.loads(payload), dict):
                    raise CaptureSchemaError("stored payload is not an object")
        return result


__all__ = ["CaptureSchemaError", "ForwardCaptureStore"]

"""Restart-safe SQLite/WAL store for immutable forward capture records."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from threading import Lock

from .forward_models import CAPTURE_SCHEMA_VERSION, CaptureRecord, CaptureRecordType


_SQL_BOUNDARY_MARGIN_DAYS = 1.0 / 86_400.0


class CaptureSchemaError(RuntimeError):
    pass


class ForwardCaptureStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._materialization_lock = Lock()
        self._records_materialized_total = 0
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
        return self._materialize(rows)

    def records_for_daily_report(
        self,
        *,
        start_utc: datetime,
        end_utc: datetime,
    ) -> tuple[CaptureRecord, ...]:
        """Return the sequence-ordered subset needed by one daily report."""

        _validate_utc_window(start_utc, end_utc)
        columns = (
            "sequence,schema_version,record_id,record_type,symbol,timestamp,"
            "payload_json"
        )
        # SQLite time conversion is deliberately only a conservative prefilter.
        # Python retains authority over exact Eastern-day membership, so widen
        # both boundaries to prevent conversion precision from dropping a row.
        query = f"""
            SELECT schema_version,record_id,record_type,symbol,timestamp,payload_json
            FROM (
                SELECT {columns}
                FROM capture_records
                WHERE record_type=?
                UNION ALL
                SELECT {columns}
                FROM capture_records
                WHERE record_type IN (?,?)
                  AND julianday(timestamp) <
                      julianday(?) + {_SQL_BOUNDARY_MARGIN_DAYS!r}
                UNION ALL
                SELECT {columns}
                FROM capture_records
                WHERE record_type IN (?,?,?)
                  AND julianday(timestamp) >=
                      julianday(?) - {_SQL_BOUNDARY_MARGIN_DAYS!r}
                  AND julianday(timestamp) <
                      julianday(?) + {_SQL_BOUNDARY_MARGIN_DAYS!r}
            ) AS daily_report_records
            ORDER BY sequence
        """
        values = (
            CaptureRecordType.OBSERVATION_SESSION.value,
            CaptureRecordType.PAPER_FILL.value,
            CaptureRecordType.COUNTERFACTUAL.value,
            end_utc.isoformat(),
            CaptureRecordType.DISCOVERY.value,
            CaptureRecordType.STATE_TRANSITION.value,
            CaptureRecordType.DATA_QUALITY.value,
            start_utc.isoformat(),
            end_utc.isoformat(),
        )
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return self._materialize(rows)

    def record_timestamps_between(
        self,
        *,
        start_utc: datetime,
        end_utc: datetime,
        exclude_record_type: CaptureRecordType | None = None,
    ) -> tuple[datetime, ...]:
        """Return timestamp values in a conservative UTC window without payloads."""

        _validate_utc_window(start_utc, end_utc)
        clause = ""
        values: list[str] = [start_utc.isoformat(), end_utc.isoformat()]
        if exclude_record_type is not None:
            clause = " AND record_type<>?"
            values.append(exclude_record_type.value)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT timestamp FROM capture_records "
                "WHERE julianday(timestamp) >= "
                f"julianday(?) - {_SQL_BOUNDARY_MARGIN_DAYS!r} "
                "AND julianday(timestamp) < "
                f"julianday(?) + {_SQL_BOUNDARY_MARGIN_DAYS!r}"
                f"{clause} ORDER BY sequence",
                values,
            ).fetchall()
        return tuple(datetime.fromisoformat(row[0]) for row in rows)

    def _materialize(self, rows: list[tuple]) -> tuple[CaptureRecord, ...]:
        with self._materialization_lock:
            self._records_materialized_total += len(rows)
        return tuple(CaptureRecord(
            row[0], row[1], CaptureRecordType(row[2]), row[3],
            datetime.fromisoformat(row[4]), row[5],
        ) for row in rows)

    def records_materialized_total(self) -> int:
        """Cumulative rows returned by ``records`` without retaining them."""

        with self._materialization_lock:
            return self._records_materialized_total

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


def _validate_utc_window(start_utc: datetime, end_utc: datetime) -> None:
    if (
        start_utc.tzinfo is None
        or end_utc.tzinfo is None
        or start_utc.utcoffset() is None
        or end_utc.utcoffset() is None
        or start_utc.utcoffset().total_seconds() != 0
        or end_utc.utcoffset().total_seconds() != 0
        or start_utc >= end_utc
    ):
        raise ValueError("daily report bounds must be an increasing UTC window")


__all__ = ["CaptureSchemaError", "ForwardCaptureStore"]

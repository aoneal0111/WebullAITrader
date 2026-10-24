"""Bounded SQLite reads without importing research, GUI or provider packages."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time


@contextmanager
def read_database(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        deadline = time.monotonic() + 10
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute("PRAGMA query_only=ON")
        yield db
    finally:
        db.close()


def bounded_rows(db, query, args, limit, byte_limit):
    rows, size = [], 0
    for row in db.execute(query, args):
        size += sum(len(str(v).encode("utf-8")) for v in row)
        if len(rows) >= limit or size > byte_limit:
            raise ValueError("DATABASE_RESULT_LIMIT")
        rows.append(row)
    return rows

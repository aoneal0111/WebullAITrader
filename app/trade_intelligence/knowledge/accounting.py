"""Durable, offline status accounting for research acquisition runs."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from .acquisition import atomic_write_jsonl
from .models import ACTIVE_STRATEGIES


RUN_SCHEMA_VERSION = 1


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError, TypeError):
        return default


def _bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _partition_state(root: Path) -> dict[str, int]:
    counts = {"planned": 0, "in_progress": 0, "complete": 0, "empty_confirmed": 0,
              "failed_transient": 0, "failed_permanent": 0, "corrupt": 0}
    manifests = root / "manifests"
    if not manifests.exists():
        return counts
    for path in manifests.glob("*.json"):
        row = _read(path, {})
        status = str(row.get("status", "")).lower()
        if status == "empty_confirmed": counts["empty_confirmed"] += 1
        elif status == "complete": counts["complete"] += 1
        elif status == "in_progress": counts["in_progress"] += 1
        elif status == "failed_transient": counts["failed_transient"] += 1
        elif status == "failed_permanent": counts["failed_permanent"] += 1
        elif status == "corrupt": counts["corrupt"] += 1
    counts["planned"] = sum(counts.values())
    return counts


def status_for_root(root: Path, *, persist_reconstruction: bool = True) -> dict[str, object]:
    """Return complete local status without credentials or network access."""
    root = Path(root)
    corpus_candidates = [root / "corpus"] if (root / "corpus").exists() else []
    if root.exists():
        corpus_candidates.extend(path for path in root.iterdir()
                                 if path.is_dir() and (path / "manifest.json").exists())
    corpus = max(corpus_candidates, key=lambda path: (path / "manifest.json").stat().st_mtime_ns) if corpus_candidates else root
    run_path = root / "run_manifest.json"
    run = _read(run_path, {})
    partitions = _partition_state(root)
    manifests = [_read(path, {}) for path in (root / "manifests").glob("*.json")] if (root / "manifests").exists() else []
    complete = [row for row in manifests if row.get("status") in ("COMPLETE", "EMPTY_CONFIRMED")]
    dates = sorted({str(row.get("trading_date")) for row in manifests if row.get("trading_date")})
    symbols = sorted({str(row.get("symbol")) for row in manifests if row.get("symbol")})
    raw_rows = sum(int(row.get("row_count") or 0) for row in complete)
    normalized_rows = sum(len((root / "normalized" / f"{row.get('symbol')}_{row.get('trading_date')}.jsonl").read_text(encoding="utf-8").splitlines())
                          for row in complete if (root / "normalized" / f"{row.get('symbol')}_{row.get('trading_date')}.jsonl").exists())
    corpus_manifest = _read(corpus / "manifest.json", {})
    summary = corpus_manifest.get("summary", {})
    mined_index = _read_jsonl(corpus / "mined_partitions.jsonl")
    mined_rows = tuple(mined_index)
    mining_partitions = {"planned": 0, "in_progress": 0, "complete": 0, "failed": 0, "stale_input": 0}
    for row in mined_rows:
        state = str(row.get("status", "")).lower()
        if state in mining_partitions:
            mining_partitions[state] += 1
    if run and summary:
        run["corpus"] = {"unique_episodes": summary.get("accepted_unique", 0),
                          "strategy_memberships": summary.get("strategy_memberships", 0),
                          "quarantined": summary.get("quarantined", 0),
                          "exact_duplicates": summary.get("exact_duplicates", 0),
                          "near_duplicates": summary.get("near_duplicates", 0)}
    if not run and manifests:
        seed = "|".join(sorted(str(path) + str(path.stat().st_mtime_ns) for path in (root / "manifests").glob("*.json")))
        run = {"run_id": "reconstructed-" + hashlib.sha256(seed.encode()).hexdigest()[:16],
               "schema_version": RUN_SCHEMA_VERSION, "provider": complete[0].get("provider", "UNKNOWN") if complete else None,
               "feed": complete[0].get("feed", "UNKNOWN") if complete else None,
               "start_date": dates[0] if dates else None, "end_date": dates[-1] if dates else None,
               "symbols": symbols, "started_at": complete[0].get("downloaded_at") if complete else None,
               "finished_at": complete[-1].get("downloaded_at") if complete else None, "final_status": "SUCCEEDED",
               "partitions": partitions, "requests": {"market_data": len(complete), "pages": len(complete), "retries": 0, "429": 0, "5xx": 0},
               "rows": {"raw": raw_rows, "normalized": normalized_rows}, "storage": {
                   "raw_bytes": _bytes(root / "raw"), "normalized_bytes": _bytes(root / "normalized"), "corpus_bytes": _bytes(corpus)},
               "corpus": {"unique_episodes": summary.get("accepted_unique", sum(1 for _ in _read_jsonl(corpus / "episodes.jsonl"))),
                          "strategy_memberships": summary.get("strategy_memberships", sum(1 for _ in _read_jsonl(corpus / "memberships.jsonl"))),
                          "quarantined": summary.get("quarantined", sum(1 for _ in _read_jsonl(corpus / "quarantine.jsonl"))),
                          "exact_duplicates": summary.get("exact_duplicates", 0), "near_duplicates": summary.get("near_duplicates", 0)},
               "last_failure": None}
        if persist_reconstruction:
            atomic_write_jsonl(run_path, (run,))
    quota = {strategy: 0 for strategy in ACTIVE_STRATEGIES}
    store_status = _read(corpus / "status.json", {})
    if not store_status:
        for row in _read_jsonl(corpus / "episodes.jsonl"):
            for strategy in row.get("strategy_memberships", ()):
                quota[strategy] = quota.get(strategy, 0) + 1
    else:
        quota.update(store_status.get("per_strategy", {}))
    if run:
        run.setdefault("quota", {strategy: {"accepted": quota.get(strategy, 0), "remaining": max(0, 5000 - quota.get(strategy, 0)),
                                             "satisfied": quota.get(strategy, 0) >= 5000} for strategy in ACTIVE_STRATEGIES})
    run.update({"run_status": run.get("final_status"), "last_successful_run": run.get("run_id") if run.get("final_status") == "SUCCEEDED" else None,
                "partitions": partitions, "quota": {strategy: {"accepted": quota.get(strategy, 0),
                    "remaining": max(0, 5000 - quota.get(strategy, 0)), "satisfied": quota.get(strategy, 0) >= 5000}
                    for strategy in ACTIVE_STRATEGIES}})
    run["corpus"] = {"unique_episodes": summary.get("accepted_unique", sum(1 for _ in _read_jsonl(corpus / "episodes.jsonl"))),
                     "strategy_memberships": summary.get("strategy_memberships", sum(1 for _ in _read_jsonl(corpus / "memberships.jsonl"))),
                     "quarantined": summary.get("quarantined", sum(1 for _ in _read_jsonl(corpus / "quarantine.jsonl"))),
                     "exact_duplicates": summary.get("exact_duplicates", 0), "near_duplicates": summary.get("near_duplicates", 0)}
    run["mining_partitions"] = mining_partitions
    return run


def _read_jsonl(path: Path):
    if not path.exists():
        return iter(())
    def rows():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
    return rows()

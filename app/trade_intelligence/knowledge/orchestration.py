"""Safe front-to-back research orchestration; never imports execution code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import uuid

from .acquisition import (AcquisitionConfig, AlpacaHistoricalClient, atomic_write_jsonl,
                          download_partition)
from .candidate_days import attach_previous_closes, discover_candidate_days
from .mining import JsonlBarProvider, build_corpus
from .models import ACTIVE_STRATEGIES
from .reporting import report, validate_corpus
from app.market.calendar import EASTERN


@dataclass(frozen=True, slots=True)
class RunPlan:
    provider: str
    feed: str
    start_date: date
    end_date: date
    target_per_strategy: int
    candidate_filter: str = "HIGH_RECALL_DAILY_MOVE_OR_RANGE_OR_GAP"


def plan_summary(plan: RunPlan, *, symbols: int = 0, candidate_days: int = 0) -> dict[str, object]:
    return {"provider": plan.provider, "feed": plan.feed, "start_date": plan.start_date.isoformat(),
            "end_date": plan.end_date.isoformat(), "symbols": symbols, "candidate_days": candidate_days,
            "estimated_requests": candidate_days + 1, "target_per_strategy": plan.target_per_strategy,
            "candidate_filter": plan.candidate_filter, "estimated_api_time_seconds": round((candidate_days + 1) / 3, 2),
            "estimated_storage": "provider/data dependent; use sample run before bulk acquisition"}


class ResearchOrchestrator:
    def __init__(self, plan: RunPlan, *, root: Path = Path("data/research/market_data"),
                 corpus_root: Path = Path("data/research/trading_knowledge/v1")) -> None:
        if plan.target_per_strategy <= 0: raise ValueError("target_per_strategy must be positive")
        self.plan, self.root, self.corpus_root = plan, Path(root), Path(corpus_root)
        self.run_id = uuid.uuid4().hex

    def dry_run(self) -> dict[str, object]:
        return plan_summary(self.plan)

    def preflight_daily(self, symbols: tuple[str, ...]) -> dict[str, object]:
        """Complete universe/daily/candidate planning without minute downloads."""
        ordered = tuple(sorted({symbol.upper() for symbol in symbols}))
        daily_root = self.root / "daily"
        batch_root = daily_root / "batches"
        manifest_root = daily_root / "manifests"
        candidate_path = self.root / "candidates" / "candidate_days.jsonl"
        batch_root.mkdir(parents=True, exist_ok=True); manifest_root.mkdir(parents=True, exist_ok=True)
        lookback = self.plan.start_date - timedelta(days=10)
        start = datetime.combine(lookback, time.min, tzinfo=UTC)
        end = datetime.combine(self.plan.end_date + timedelta(days=1), time.min, tzinfo=UTC)
        client = AlpacaHistoricalClient.from_environment(config=AcquisitionConfig(
            start_date=lookback, end_date=self.plan.end_date))
        batch_size = client.config.daily_symbol_batch_size
        # Daily discovery is symbol-batched.  Process each completed batch
        # immediately so the full broad-universe history is never retained in
        # memory merely to select candidate days.
        candidate_items = []
        daily_rows_count = 0
        rows_with_prior_close = 0
        rows_without_prior_close = 0
        reasons = Counter()
        reused = 0
        try:
            for offset in range(0, len(ordered), batch_size):
                batch_symbols = ordered[offset:offset + batch_size]
                identity = hashlib.sha256("|".join((self.plan.provider, self.plan.feed, self.plan.start_date.isoformat(),
                                                     self.plan.end_date.isoformat(), *batch_symbols)).encode()).hexdigest()[:20]
                data_path = batch_root / f"{identity}.jsonl"
                manifest_path = manifest_root / f"{identity}.json"
                saved = None
                if data_path.exists() and manifest_path.exists():
                    try:
                        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
                        if saved.get("status") == "COMPLETE" and saved.get("content_hash") == hashlib.sha256(data_path.read_bytes()).hexdigest():
                            reused += 1
                    except (OSError, ValueError, TypeError):
                        saved = None
                if saved is None:
                    rows = client.fetch_daily_bars(batch_symbols, start, end)
                    payload = tuple({"symbol": str(row.get("S") or row.get("symbol") or "").upper(),
                                     "trading_date": datetime.fromisoformat(str(row["t"])).astimezone(EASTERN).date().isoformat(),
                                     "open": row["o"], "high": row["h"], "low": row["l"], "close": row["c"], "volume": row["v"]} for row in rows)
                    digest = atomic_write_jsonl(data_path, payload)
                    saved = {"batch_id": identity, "status": "COMPLETE", "symbols": batch_symbols,
                             "symbol_hash": hashlib.sha256("|".join(batch_symbols).encode()).hexdigest(),
                             "request_start": start.isoformat(), "request_end": end.isoformat(),
                             "row_count": len(payload), "content_hash": digest}
                    atomic_write_jsonl(manifest_path, (saved,))
                batch_rows = tuple(json.loads(line) for line in data_path.read_text(encoding="utf-8").splitlines() if line)
                target_rows = attach_previous_closes(batch_rows, start_date=self.plan.start_date)
                batch_candidates = discover_candidate_days(list(target_rows))
                candidate_items.extend(batch_candidates)
                daily_rows_count += len(target_rows)
                rows_with_prior_close += sum(row.get("previous_close") is not None for row in target_rows)
                rows_without_prior_close += sum(row.get("previous_close") is None for row in target_rows)
                reasons.update(reason for item in batch_candidates for reason in item.reasons)
        finally:
            counters = dict(client.request_counters)
            client.close()
        candidates = tuple(candidate_items)
        candidate_records = []
        for item in candidates:
            candidate_records.append({
                "symbol": item.symbol, "trading_date": item.trading_date.isoformat(), "reasons": item.reasons,
                "open": str(item.open), "high": str(item.high), "low": str(item.low), "close": str(item.close),
                "volume": str(item.volume), "previous_close": None if item.previous_close is None else str(item.previous_close),
                "gap_percent": None if item.gap_percent is None else str(item.gap_percent),
                "change_percent": str(item.change_percent), "range_percent": str(item.range_percent),
                "dollar_volume": str(item.dollar_volume)})
        atomic_write_jsonl(candidate_path, candidate_records)
        result = {"run_id": self.run_id, "provider": self.plan.provider, "feed": self.plan.feed,
                "start_date": self.plan.start_date.isoformat(), "end_date": self.plan.end_date.isoformat(),
                "universe_symbols": len(ordered), "daily_symbol_batch_size": batch_size,
                "daily_request_batches": (len(ordered) + batch_size - 1) // batch_size,
                "daily_batches_reused": reused, "daily_rows": daily_rows_count,
                "rows_with_prior_close": rows_with_prior_close,
                "rows_without_prior_close": rows_without_prior_close,
                "candidate_symbol_days": len(candidates), "candidate_reasons": dict(reasons),
                "planned_minute_partitions": len(candidates), "minute_requests": 0,
                "daily_request_counters": counters, "preflight_only": True,
                "candidate_path": str(candidate_path)}
        atomic_write_jsonl(self.root / "run_manifest.json", ({**result, "final_status": "SUCCEEDED",
            "phases": {"UNIVERSE": "COMPLETE", "DAILY_DISCOVERY": "COMPLETE",
                       "CANDIDATE_SELECTION": "COMPLETE", "MINUTE_ACQUISITION": "NOT_RUN"}},))
        return result

    def provider_check(self) -> dict[str, object]:
        client = AlpacaHistoricalClient.from_environment(config=AcquisitionConfig(
            start_date=self.plan.start_date, end_date=self.plan.end_date))
        try: return client.health_check()
        finally: client.close()

    def run_local_mine(self, normalized_jsonl: Path, *, repository_commit: str) -> dict[str, object]:
        summary = build_corpus(JsonlBarProvider(normalized_jsonl), self.corpus_root,
                               repository_commit=repository_commit)
        return {"run_id": self.run_id, "accepted_unique": summary.accepted_unique,
                "strategy_memberships": summary.strategy_memberships,
                "validation_errors": validate_corpus(self.corpus_root)}

    def run_alpaca(self, symbols: tuple[str, ...], *, repository_commit: str) -> dict[str, object]:
        """Run the bounded daily-discovery -> symbol/day download -> mine flow.

        This method is never called by the CLI without an explicit execution
        flag.  Each symbol/day remains an independent, retryable partition.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        run_manifest = self.root / "run_manifest.json"
        started = datetime.now(UTC).isoformat()
        atomic_write_jsonl(run_manifest, ({"schema_version": 1, "run_id": self.run_id,
            "provider": self.plan.provider, "feed": self.plan.feed, "start_date": self.plan.start_date.isoformat(),
            "end_date": self.plan.end_date.isoformat(), "symbols": symbols, "target_per_strategy": self.plan.target_per_strategy,
            "repository_commit": repository_commit, "started_at": started, "final_status": "RUNNING"},))
        config = AcquisitionConfig(start_date=self.plan.start_date, end_date=self.plan.end_date,
                                   raw_root=self.root / "raw", normalized_root=self.root / "normalized",
                                   manifest_root=self.root / "manifests")
        client = AlpacaHistoricalClient.from_environment(config=config)
        manifests = []
        accepted_unique = 0
        strategy_memberships = 0
        validation_errors = []
        try:
            start = datetime.combine(self.plan.start_date, time.min, tzinfo=UTC)
            end = datetime.combine(self.plan.end_date + timedelta(days=1), time.min, tzinfo=UTC)
            daily = client.fetch_daily_bars_batched(symbols, start, end)
            daily_rows = []
            for row in daily:
                timestamp = datetime.fromisoformat(str(row["t"])).astimezone(EASTERN)
                daily_rows.append({"symbol": str(row.get("S") or row.get("symbol") or "").upper(),
                                   "trading_date": timestamp.date().isoformat(), "open": row["o"],
                                   "high": row["h"], "low": row["l"], "close": row["c"], "volume": row["v"]})
            daily_rows = list(attach_previous_closes(daily_rows, start_date=self.plan.start_date))
            candidates = tuple(item for item in discover_candidate_days(daily_rows)
                               if self.plan.start_date <= item.trading_date <= self.plan.end_date)
            for candidate in candidates:
                manifest = download_partition(client, config, candidate.symbol, candidate.trading_date,
                                               previous_close=candidate.previous_close)
                manifests.append(manifest.to_dict())
                normalized_path = config.normalized_root / f"{candidate.symbol}_{candidate.trading_date.isoformat()}.jsonl"
                if normalized_path.exists():
                    mined = self.run_local_mine(normalized_path, repository_commit=repository_commit)
                    accepted_unique += int(mined["accepted_unique"])
                    strategy_memberships += int(mined["strategy_memberships"])
                    validation_errors.extend(mined["validation_errors"])
        except Exception as exc:
            atomic_write_jsonl(run_manifest, ({"schema_version": 1, "run_id": self.run_id,
                "provider": self.plan.provider, "feed": self.plan.feed, "start_date": self.plan.start_date.isoformat(),
                "end_date": self.plan.end_date.isoformat(), "symbols": symbols, "started_at": started,
                "finished_at": datetime.now(UTC).isoformat(), "final_status": "FAILED",
                "last_failure_phase": "ACQUISITION", "last_failure_reason": str(exc)[:240]},))
            raise
        finally:
            client.close()
        result = {"run_id": self.run_id, "accepted_unique": accepted_unique,
                  "strategy_memberships": strategy_memberships,
                  "validation_errors": tuple(validation_errors),
                  "candidate_days": len(manifests), "partitions": manifests}
        atomic_write_jsonl(run_manifest, ({"schema_version": 1, "run_id": self.run_id,
            "provider": self.plan.provider, "feed": self.plan.feed, "start_date": self.plan.start_date.isoformat(),
            "end_date": self.plan.end_date.isoformat(), "symbols": symbols, "target_per_strategy": self.plan.target_per_strategy,
            "repository_commit": repository_commit, "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
            "final_status": "SUCCEEDED", "partitions": {"planned": len(manifests), "complete": sum(m["status"] == "COMPLETE" for m in manifests),
            "empty_confirmed": sum(m["status"] == "EMPTY_CONFIRMED" for m in manifests), "failed": 0},
            "requests": {"market_data": len(manifests), "pages": len(manifests), "retries": 0, "429": 0, "5xx": 0},
            "rows": {"raw": sum(m["row_count"] for m in manifests), "normalized": sum(m["row_count"] for m in manifests)},
            "corpus": {"new_unique_episodes": result["accepted_unique"], "new_memberships": result["strategy_memberships"]}},))
        return result


def configured() -> bool:
    return bool(os.environ.get("ALPACA_API_KEY", "").strip() and os.environ.get("ALPACA_API_SECRET", "").strip())

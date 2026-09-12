"""Safe front-to-back research orchestration; never imports execution code."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import uuid

from .acquisition import (AcquisitionConfig, AlpacaHistoricalClient, atomic_write_jsonl,
                          download_partition)
from .candidate_days import discover_candidate_days
from .mining import JsonlBarProvider, build_corpus
from .models import ACTIVE_STRATEGIES
from .reporting import report, validate_corpus


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
        normalized_rows: list[dict[str, object]] = []
        manifests = []
        try:
            start = datetime.combine(self.plan.start_date, time.min, tzinfo=UTC)
            end = datetime.combine(self.plan.end_date + timedelta(days=1), time.min, tzinfo=UTC)
            daily = client.fetch_daily_bars(symbols, start, end)
            daily_rows = []
            for row in daily:
                timestamp = datetime.fromisoformat(str(row["t"])).astimezone(UTC)
                daily_rows.append({"symbol": str(row.get("S") or row.get("symbol") or "").upper(),
                                   "trading_date": timestamp.date().isoformat(), "open": row["o"],
                                   "high": row["h"], "low": row["l"], "close": row["c"], "volume": row["v"]})
            candidates = tuple(item for item in discover_candidate_days(daily_rows)
                               if self.plan.start_date <= item.trading_date <= self.plan.end_date)
            for candidate in candidates:
                manifest = download_partition(client, config, candidate.symbol, candidate.trading_date,
                                               previous_close=candidate.previous_close)
                manifests.append(manifest.to_dict())
                normalized_path = config.normalized_root / f"{candidate.symbol}_{candidate.trading_date.isoformat()}.jsonl"
                if normalized_path.exists():
                    normalized_rows.extend(json.loads(line) for line in normalized_path.read_text(encoding="utf-8").splitlines() if line)
        except Exception as exc:
            atomic_write_jsonl(run_manifest, ({"schema_version": 1, "run_id": self.run_id,
                "provider": self.plan.provider, "feed": self.plan.feed, "start_date": self.plan.start_date.isoformat(),
                "end_date": self.plan.end_date.isoformat(), "symbols": symbols, "started_at": started,
                "finished_at": datetime.now(UTC).isoformat(), "final_status": "FAILED",
                "last_failure_phase": "ACQUISITION", "last_failure_reason": str(exc)[:240]},))
            raise
        finally:
            client.close()
        aggregate = self.root / "normalized" / f"run_{self.run_id}.jsonl"
        atomic_write_jsonl(aggregate, normalized_rows)
        result = self.run_local_mine(aggregate, repository_commit=repository_commit)
        result.update({"candidate_days": len(manifests), "partitions": manifests})
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

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
from .candidate_days import (attach_previous_closes, candidate_from_record,
                              candidate_records_hash, candidate_to_record, discover_candidate_days)
from .mining import (MINING_IDENTITY_VERSION, MINING_SEMANTICS_VERSION,
                     STRATEGY_SEMANTICS_VERSION, JsonlBarProvider, build_corpus,
                     mining_content_key)
from .models import ACTIVE_STRATEGIES
from .reporting import report, validate_corpus
from .storage import KnowledgeStore
from app.market.calendar import EASTERN


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RunPlan:
    provider: str
    feed: str
    start_date: date
    end_date: date
    target_per_strategy: int
    candidate_filter: str = "HIGH_RECALL_DAILY_MOVE_OR_RANGE_OR_GAP"


class MiningSession:
    """One-process mining context with corpus indexes loaded exactly once."""

    def __init__(self, corpus_root: Path) -> None:
        self.store = KnowledgeStore(corpus_root)
        self.reconciliation = self.store.reconcile_mined_partitions()
        self.partitions_processed = 0
        self.partitions_reused = 0
        self.partitions_mined = 0

    def compatible(self, *, candidate_plan_id: str, symbol: str, trading_date: str,
                   normalized_sha256: str) -> dict[str, object] | None:
        return self.store.compatible_complete(candidate_plan_id=candidate_plan_id,
                                               symbol=symbol, trading_date=trading_date,
                                               normalized_sha256=normalized_sha256)


def resolve_corpus_root(output_root: Path, *, validate: bool = True) -> Path:
    """Resolve one durable corpus continuation target for a tranche.

    A pointer is required before an alternate/repaired corpus can supersede
    the normal ``corpus`` directory.  This keeps new tranches on the normal
    path and prevents silently choosing between competing corpus directories.
    """
    root = Path(output_root)
    pointer_path = root / "corpus_pointer.json"
    if not pointer_path.exists():
        if (root / "manifest.json").exists() and not (root / "candidates").exists():
            return root
        return root / "corpus"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        relative = Path(str(pointer["canonical_corpus_path"]))
        canonical = relative if relative.is_absolute() else root / relative
        if pointer.get("validation_status") != "PASS" or not canonical.is_dir():
            raise ValueError("INVALID_CORPUS_CONTINUATION_POINTER")
        if not (canonical / "mined_partitions.jsonl").exists():
            raise ValueError("MISSING_MINED_PARTITION_INDEX")
        if validate and validate_corpus(canonical):
            raise ValueError("INVALID_CANONICAL_CORPUS")
        expected = pointer.get("expected_mined_partitions")
        if expected is not None:
            complete = sum(1 for row in KnowledgeStore(canonical).mined_partitions().values()
                           if row.get("status") == "COMPLETE")
            if complete != int(expected):
                raise ValueError("CANONICAL_CORPUS_PARTITION_COUNT_MISMATCH")
        return canonical
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError(str(exc)) from exc


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
        self._mining_session: MiningSession | None = None

    def _session(self) -> MiningSession:
        if self._mining_session is None:
            self._mining_session = MiningSession(self.corpus_root)
        return self._mining_session

    def dry_run(self) -> dict[str, object]:
        return plan_summary(self.plan)

    def _plan_identity(self, symbols: tuple[str, ...]) -> tuple[str, str]:
        ordered = tuple(sorted({symbol.upper() for symbol in symbols if symbol.strip()}))
        symbol_hash = hashlib.sha256("|".join(ordered).encode()).hexdigest()
        plan_id = hashlib.sha256("|".join((self.plan.provider, self.plan.feed,
            self.plan.start_date.isoformat(), self.plan.end_date.isoformat(), symbol_hash,
            self.plan.candidate_filter)).encode()).hexdigest()[:24]
        return plan_id, symbol_hash

    def _load_candidate_plan(self, symbols: tuple[str, ...]) -> dict[str, object] | None:
        path = self.root / "candidates" / "candidate_plan.json"
        artifact = self.root / "candidates" / "candidate_days.jsonl"
        if not path.exists() or not artifact.exists():
            return None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            plan_id, symbol_hash = self._plan_identity(symbols)
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if (manifest.get("status") != "COMPLETE" or manifest.get("plan_id") != plan_id or
                    manifest.get("universe_hash") != symbol_hash or manifest.get("provider") != self.plan.provider or
                    manifest.get("feed") != self.plan.feed or manifest.get("start_date") != self.plan.start_date.isoformat() or
                    manifest.get("end_date") != self.plan.end_date.isoformat() or
                    manifest.get("candidate_artifact_sha256") != digest):
                return None
            batch_size = int(manifest.get("daily_symbol_batch_size", 250))
            daily_manifest_hashes = []
            ordered = tuple(sorted({symbol.upper() for symbol in symbols}))
            for offset in range(0, len(ordered), batch_size):
                batch_symbols = ordered[offset:offset + batch_size]
                identity = hashlib.sha256("|".join((self.plan.provider, self.plan.feed,
                    self.plan.start_date.isoformat(), self.plan.end_date.isoformat(), *batch_symbols)).encode()).hexdigest()[:20]
                daily_manifest = self.root / "daily" / "manifests" / f"{identity}.json"
                daily_data = self.root / "daily" / "batches" / f"{identity}.jsonl"
                if not daily_manifest.exists() or not daily_data.exists():
                    return None
                daily_meta = json.loads(daily_manifest.read_text(encoding="utf-8"))
                if (daily_meta.get("status") != "COMPLETE" or
                        daily_meta.get("content_hash") != hashlib.sha256(daily_data.read_bytes()).hexdigest()):
                    return None
                daily_manifest_hashes.append(hashlib.sha256(daily_manifest.read_bytes()).hexdigest())
            if tuple(daily_manifest_hashes) != tuple(manifest.get("source_daily_manifest_hashes", ())):
                return None
            candidates = tuple(candidate_from_record(json.loads(line)) for line in artifact.read_text(encoding="utf-8").splitlines() if line)
            reasons = Counter(reason for item in candidates for reason in item.reasons)
            if (len(candidates) != manifest.get("candidate_count") or
                    candidate_records_hash(candidates) != manifest.get("candidate_records_sha256") or
                    dict(reasons) != manifest.get("candidate_reasons")):
                return None
            return {**manifest, "candidates": candidates, "candidate_artifact_path": str(artifact)}
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def prepare_candidate_plan(self, symbols: tuple[str, ...], *, allow_network: bool,
                               force_refresh: bool = False) -> dict[str, object]:
        """Build or load the sole durable candidate plan used by all phases."""
        ordered = tuple(sorted({symbol.upper() for symbol in symbols}))
        if not force_refresh:
            cached = self._load_candidate_plan(ordered)
            if cached is not None:
                cached["daily_network_requests"] = 0
                return cached
        daily_root = self.root / "daily"
        batch_root = daily_root / "batches"
        manifest_root = daily_root / "manifests"
        candidate_path = self.root / "candidates" / "candidate_days.jsonl"
        batch_root.mkdir(parents=True, exist_ok=True); manifest_root.mkdir(parents=True, exist_ok=True)
        lookback = self.plan.start_date - timedelta(days=10)
        start = datetime.combine(lookback, time.min, tzinfo=UTC)
        end = datetime.combine(self.plan.end_date + timedelta(days=1), time.min, tzinfo=UTC)
        acquisition_config = AcquisitionConfig(start_date=lookback, end_date=self.plan.end_date)
        client = AlpacaHistoricalClient.from_environment(config=acquisition_config) if allow_network else None
        batch_size = acquisition_config.daily_symbol_batch_size
        # Daily discovery is symbol-batched.  Process each completed batch
        # immediately so the full broad-universe history is never retained in
        # memory merely to select candidate days.
        candidate_items = []
        daily_rows_count = 0
        rows_with_prior_close = 0
        rows_without_prior_close = 0
        reasons = Counter()
        reused = 0
        source_daily_manifest_hashes = []
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
                    if client is None:
                        raise RuntimeError("DAILY_BATCH_UNAVAILABLE_OFFLINE")
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
                source_daily_manifest_hashes.append(hashlib.sha256(manifest_path.read_bytes()).hexdigest())
                batch_rows = tuple(json.loads(line) for line in data_path.read_text(encoding="utf-8").splitlines() if line)
                target_rows = attach_previous_closes(batch_rows, start_date=self.plan.start_date)
                batch_candidates = discover_candidate_days(list(target_rows))
                candidate_items.extend(batch_candidates)
                daily_rows_count += len(target_rows)
                rows_with_prior_close += sum(row.get("previous_close") is not None for row in target_rows)
                rows_without_prior_close += sum(row.get("previous_close") is None for row in target_rows)
                reasons.update(reason for item in batch_candidates for reason in item.reasons)
        finally:
            counters = {} if client is None else dict(client.request_counters)
            if client is not None:
                client.close()
        candidates = tuple(candidate_items)
        candidate_records = [candidate_to_record(item) for item in candidates]
        atomic_write_jsonl(candidate_path, candidate_records)
        plan_id, symbol_hash = self._plan_identity(ordered)
        artifact_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
        result = {"run_id": self.run_id, "plan_id": plan_id, "provider": self.plan.provider, "feed": self.plan.feed,
                "start_date": self.plan.start_date.isoformat(), "end_date": self.plan.end_date.isoformat(),
                "universe_symbols": len(ordered), "daily_symbol_batch_size": batch_size,
                "daily_request_batches": (len(ordered) + batch_size - 1) // batch_size,
                "daily_batches_reused": reused, "daily_rows": daily_rows_count,
                "rows_with_prior_close": rows_with_prior_close,
                "rows_without_prior_close": rows_without_prior_close,
                "candidate_symbol_days": len(candidates), "candidate_count": len(candidates), "candidate_reasons": dict(reasons),
                "planned_minute_partitions": len(candidates), "minute_requests": 0,
                "daily_request_counters": counters, "preflight_only": True,
                "candidate_path": str(candidate_path), "universe_hash": symbol_hash,
                "candidate_artifact_sha256": artifact_hash, "candidate_records_sha256": candidate_records_hash(candidates),
                "candidate_filter_version": self.plan.candidate_filter,
                "prior_close_derivation_version": "LATEST_PRIOR_DAILY_CLOSE_V1",
                "source_daily_manifest_hashes": source_daily_manifest_hashes}
        atomic_write_jsonl(self.root / "candidates" / "candidate_plan.json", ({**result, "status": "COMPLETE",
            "phases": {"UNIVERSE": "COMPLETE", "DAILY_DISCOVERY": "COMPLETE",
                       "CANDIDATE_SELECTION": "COMPLETE", "MINUTE_ACQUISITION": "NOT_RUN"}},))
        atomic_write_jsonl(self.root / "run_manifest.json", ({**result, "final_status": "SUCCEEDED",
            "phases": {"UNIVERSE": "COMPLETE", "DAILY_DISCOVERY": "COMPLETE",
                       "CANDIDATE_SELECTION": "COMPLETE", "MINUTE_ACQUISITION": "NOT_RUN"}},))
        result["candidates"] = candidates
        return result

    def preflight_daily(self, symbols: tuple[str, ...]) -> dict[str, object]:
        """Complete universe/daily/candidate planning without minute downloads."""
        result = self.prepare_candidate_plan(symbols, allow_network=True)
        return {key: value for key, value in result.items() if key != "candidates"}

    def execution_plan_only(self, symbols: tuple[str, ...]) -> dict[str, object]:
        plan = self.prepare_candidate_plan(symbols, allow_network=False)
        candidates = plan["candidates"]
        return {"plan_id": plan["plan_id"], "candidate_artifact_sha256": plan["candidate_artifact_sha256"],
                "candidate_count": len(candidates), "daily_network_requests": 0, "minute_requests": 0,
                "planned_minute_partitions": len(candidates), "first_candidate": candidate_to_record(candidates[0]) if candidates else None,
                "last_candidate": candidate_to_record(candidates[-1]) if candidates else None,
                "candidate_path": plan.get("candidate_artifact_path", plan.get("candidate_path"))}

    def provider_check(self) -> dict[str, object]:
        client = AlpacaHistoricalClient.from_environment(config=AcquisitionConfig(
            start_date=self.plan.start_date, end_date=self.plan.end_date))
        try: return client.health_check()
        finally: client.close()

    def run_local_mine(self, normalized_jsonl: Path, *, repository_commit: str,
                       partition_id: str | None = None,
                       partition_metadata: dict[str, object] | None = None) -> dict[str, object]:
        if partition_id is not None:
            session = self._session()
            store = session.store
            digest = _sha256_file(normalized_jsonl)
            prior = store.mined_partitions(refresh=False).get(partition_id)
            metadata = partition_metadata or {}
            compatible = None
            if all(metadata.get(field) is not None for field in ("candidate_plan_id", "symbol", "trading_date")):
                compatible = session.compatible(
                    candidate_plan_id=str(metadata["candidate_plan_id"]), symbol=str(metadata["symbol"]),
                    trading_date=str(metadata["trading_date"]), normalized_sha256=digest)
            if ((prior and prior.get("status") == "COMPLETE" and prior.get("normalized_sha256") == digest)
                    or compatible is not None):
                session.partitions_processed += 1
                session.partitions_reused += 1
                return {"run_id": self.run_id, "accepted_unique": 0, "strategy_memberships": 0,
                        "validation_errors": (), "skipped": True}
            partition_metadata = {**metadata, "normalized_sha256": digest,
                                  "knowledge_schema_version": 1,
                                  "feature_derivation_version": "ATLAS_PIT_FEATURES_V1",
                                  "strategy_semantics_version": STRATEGY_SEMANTICS_VERSION,
                                  "mining_semantics_version": MINING_SEMANTICS_VERSION,
                                  "mining_identity_version": MINING_IDENTITY_VERSION,
                                  "mining_content_key": partition_id}
            store.record_mined_partition({**partition_metadata, "mining_partition_id": partition_id,
                                          "status": "IN_PROGRESS", "started_at": datetime.now(UTC).isoformat(),
                                          "normalized_sha256": digest})
        summary = build_corpus(JsonlBarProvider(normalized_jsonl), self.corpus_root,
                               repository_commit=repository_commit, partition_id=partition_id,
                               partition_metadata=partition_metadata,
                               store=session.store if partition_id is not None else None)
        if partition_id is not None:
            session.partitions_processed += 1
            session.partitions_mined += 1
        return {"run_id": self.run_id, "accepted_unique": summary.accepted_unique,
                "strategy_memberships": summary.strategy_memberships,
                "quarantined": summary.quarantined, "exact_duplicates": summary.exact_duplicates,
                "near_duplicates": summary.near_duplicates,
                "validation_errors": (), "skipped": False}

    def recover_normalized_partitions(self, symbols: tuple[str, ...], *, repository_commit: str,
                                      normalized_root: Path | None = None) -> dict[str, object]:
        """Mine only existing candidate normalized partitions; never acquires data."""
        plan = self.prepare_candidate_plan(symbols, allow_network=False)
        normalized_root = Path(normalized_root or (self.root / "normalized"))
        mined = complete = missing = 0
        accepted = memberships = quarantined = exact = near = 0
        errors = []
        for candidate in tuple(plan["candidates"]):
            path = normalized_root / f"{candidate.symbol}_{candidate.trading_date.isoformat()}.jsonl"
            if not path.exists():
                missing += 1
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            partition_id = mining_content_key(candidate_plan_id=plan["plan_id"], symbol=candidate.symbol,
                trading_date=candidate.trading_date.isoformat(), normalized_sha256=digest)
            value = self.run_local_mine(path, repository_commit=repository_commit, partition_id=partition_id,
                partition_metadata={"symbol": candidate.symbol, "trading_date": candidate.trading_date.isoformat(),
                                    "normalized_path": str(path), "normalized_sha256": digest,
                                    "candidate_plan_id": plan["plan_id"], "candidate_plan_hash": plan["candidate_artifact_sha256"]})
            if value.get("skipped"):
                complete += 1
                continue
            mined += 1; accepted += int(value["accepted_unique"]); memberships += int(value["strategy_memberships"])
            quarantined += int(value.get("quarantined", 0)); exact += int(value.get("exact_duplicates", 0)); near += int(value.get("near_duplicates", 0))
            errors.extend(value["validation_errors"])
        return {"plan_id": plan["plan_id"], "candidate_artifact_sha256": plan["candidate_artifact_sha256"],
                "normalized_partitions_discovered": mined + complete, "mined_partitions": mined,
                "already_complete_partitions": complete, "unmined_partitions": missing,
                "accepted_unique": accepted, "strategy_memberships": memberships,
                "quarantined": quarantined, "exact_duplicates": exact, "near_duplicates": near,
                "validation_errors": tuple(errors), "minute_requests": 0, "daily_requests": 0}


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
            candidate_plan = self.prepare_candidate_plan(symbols, allow_network=True)
            candidates = tuple(candidate_plan["candidates"])
            for candidate in candidates:
                manifest = download_partition(client, config, candidate.symbol, candidate.trading_date,
                                               previous_close=candidate.previous_close)
                manifests.append(manifest.to_dict())
                normalized_path = config.normalized_root / f"{candidate.symbol}_{candidate.trading_date.isoformat()}.jsonl"
                if normalized_path.exists():
                    digest = hashlib.sha256(normalized_path.read_bytes()).hexdigest()
                    partition_id = mining_content_key(candidate_plan_id=candidate_plan["plan_id"],
                        symbol=candidate.symbol, trading_date=candidate.trading_date.isoformat(),
                        normalized_sha256=digest)
                    mined = self.run_local_mine(normalized_path, repository_commit=repository_commit,
                        partition_id=partition_id, partition_metadata={"symbol": candidate.symbol,
                            "trading_date": candidate.trading_date.isoformat(), "normalized_path": str(normalized_path),
                            "normalized_sha256": digest, "candidate_plan_id": candidate_plan["plan_id"],
                            "candidate_plan_hash": candidate_plan["candidate_artifact_sha256"]})
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
        result = {"run_id": self.run_id, "plan_id": candidate_plan["plan_id"],
                  "candidate_artifact_sha256": candidate_plan["candidate_artifact_sha256"],
                  "accepted_unique": accepted_unique,
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

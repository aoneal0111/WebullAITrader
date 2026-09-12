"""Provider-neutral historical episode mining and forward-only outcome labels."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Iterable, Protocol

from app.live_scanner.session import scanner_session
from app.market.calendar import EASTERN
from app.opportunity_discovery import (
    CompletedBar, DetectionState, DiscoveryContext, FeatureCapabilities, default_registry,
)

from .identity import episode_id, near_key
from .features import feature_snapshot
from .models import (
    ACTIVE_STRATEGIES, BuildSummary, HORIZONS_SECONDS, HistoricalBar, KnowledgeEpisode,
    PERCENT_TARGETS, QuarantineRecord, R_TARGETS,
)
from .storage import KnowledgeStore


MINING_IDENTITY_VERSION = "ATLAS_MINING_IDENTITY_V2"
STRATEGY_SEMANTICS_VERSION = "ATLAS_STRATEGY_SEMANTICS_V1"
MINING_SEMANTICS_VERSION = "ATLAS_MINING_SEMANTICS_V1"


def mining_content_key(*, candidate_plan_id: str, symbol: str, trading_date: str,
                       normalized_sha256: str, knowledge_schema_version: int = 1,
                       feature_derivation_version: str = "ATLAS_PIT_FEATURES_V1",
                       strategy_semantics_version: str = STRATEGY_SEMANTICS_VERSION,
                       mining_semantics_version: str = MINING_SEMANTICS_VERSION) -> str:
    values = (MINING_IDENTITY_VERSION, candidate_plan_id, symbol, trading_date, normalized_sha256,
              str(knowledge_schema_version), feature_derivation_version,
              strategy_semantics_version, mining_semantics_version)
    return hashlib.sha256("|".join(values).encode()).hexdigest()


class HistoricalBarProvider(Protocol):
    source: str
    version: str

    def bars(self) -> Iterable[HistoricalBar]: ...


class JsonlBarProvider:
    """Read OHLCV JSONL. Optional session is honored; otherwise calendar semantics apply."""

    source = "JSONL_OHLCV"
    version = "1"
    feed = "UNKNOWN"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.errors: list[tuple[int, str]] = []

    def bars(self) -> Iterable[HistoricalBar]:
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    self.source = str(row.get("provider") or self.source)
                    self.feed = str(row.get("feed") or self.feed)
                    timestamp = datetime.fromisoformat(row["timestamp"])
                    if timestamp.tzinfo is None:
                        raise ValueError("INVALID_TIMESTAMP")
                    yield HistoricalBar(
                        str(row["symbol"]).strip().upper(), timestamp,
                        Decimal(str(row["open"])), Decimal(str(row["high"])),
                        Decimal(str(row["low"])), Decimal(str(row["close"])),
                        Decimal(str(row["volume"])),
                        str(row.get("session") or scanner_session(timestamp).value),
                        str(row.get("provider") or "UNKNOWN"), str(row.get("feed") or "UNKNOWN"),
                        str(row.get("source_timezone") or "UTC"), int(row.get("normalization_version") or 1),
                        None if row.get("previous_close") is None else Decimal(str(row["previous_close"])),
                        trade_count=None if row.get("trade_count") is None else int(row["trade_count"]),
                        provider_vwap=None if row.get("provider_vwap") is None else Decimal(str(row["provider_vwap"])),
                    )
                except Exception as exc:
                    # A bad source row is quarantined by the build; one malformed
                    # row must not discard the valid remainder of a partition.
                    self.errors.append((line_number, str(exc)[:160]))

    @property
    def identity(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def references(self) -> dict[tuple[str, str], Decimal]:
        manifest = self.path.with_name("manifest.json")
        if not manifest.exists():
            return {}
        try:
            rows = json.loads(manifest.read_text(encoding="utf-8")).get("references", ())
            return {(str(row["symbol"]).upper(), str(row["session_date"])): Decimal(str(row["previous_close"]))
                    for row in rows if row.get("previous_close") is not None}
        except (OSError, KeyError, TypeError, ValueError):
            return {}


def _completed(bar: HistoricalBar) -> CompletedBar:
    return CompletedBar(bar.symbol, bar.timestamp + timedelta(minutes=1), bar.open, bar.high,
                        bar.low, bar.close, bar.volume, bar.session)


def _quality(detection) -> Decimal:
    return sum((value for _, value in detection.quality_components), Decimal("0"))


def _outcomes(trigger: Decimal, stop: Decimal, cutoff: datetime,
              future: tuple[HistoricalBar, ...]) -> dict[str, object]:
    risk = trigger - stop
    result: dict[str, object] = {"horizons": {}, "percent_targets": {}, "r_targets": {},
                                 "hit_8_percent": False, "stop_before_8_percent": False,
                                 "same_bar_ambiguities": 0}
    result["horizons"] = {
        str(seconds): _horizon(trigger, stop, cutoff, tuple(
            bar for bar in future if bar.timestamp <= cutoff + timedelta(seconds=seconds)
        )) for seconds in HORIZONS_SECONDS
    }
    if not future:
        result["censoring_reason"] = "NO_FUTURE_BARS"
        return result
    max_high = max(bar.high for bar in future)
    min_low = min(bar.low for bar in future)
    result.update({"mfe_percent": str((max_high - trigger) / trigger * 100),
                   "mae_percent": str((min_low - trigger) / trigger * 100),
                   "mfe_per_share": str(max_high - trigger), "mae_per_share": str(min_low - trigger),
                   "mfe_r": str((max_high - trigger) / risk), "mae_r": str((min_low - trigger) / risk),
                   "maximum_R": str((max_high - trigger) / risk), "minimum_R": str((min_low - trigger) / risk)})
    for pct in PERCENT_TARGETS:
        target = trigger * (1 + Decimal(pct) / 100)
        result["percent_targets"][str(pct)] = _event_label(target, stop, cutoff, future)
    for multiple in R_TARGETS:
        target = trigger + risk * Decimal(multiple)
        result["r_targets"][multiple] = _event_label(target, stop, cutoff, future)
    eight = result["percent_targets"]["8"]
    result["hit_8_percent"] = bool(eight["hit"])
    result["stop_before_8_percent"] = eight["first_plan_event"] == "STOP"
    result["same_bar_ambiguities"] = sum(
        value["first_plan_event"] == "INTRABAR_ORDER_UNKNOWN"
        for value in result["percent_targets"].values()
    )
    hit_8 = next((bar for bar in future if bar.high >= trigger * Decimal("1.08")), None)
    if hit_8 is None:
        result["post_8"] = {"time_to_8": None, "mfe_after_8": None,
                             "maximum_giveback_after_8": None, "new_valid_setup_after_8": None}
    else:
        after_8 = tuple(bar for bar in future if bar.timestamp >= hit_8.timestamp)
        peak = max(bar.high for bar in after_8)
        trough = min(bar.low for bar in after_8)
        result["post_8"] = {
            "time_to_8": int((hit_8.timestamp - cutoff).total_seconds()),
            "mfe_after_8": str((peak - trigger) / trigger * 100),
            "maximum_giveback_after_8": str(max(Decimal("0"), (peak - trough) / peak * 100)),
            "new_valid_setup_after_8": None,
        }
    result["profit_management_research"] = {
        str(pct): _continuation_after(trigger, future, pct) for pct in (2, 3, 5, 8, 10)
    }
    return result


def _horizon(trigger: Decimal, stop: Decimal, cutoff: datetime,
             bars: tuple[HistoricalBar, ...]) -> dict[str, object]:
    if not bars:
        return {"available": False, "mfe_percent": None, "mae_percent": None,
                "target_8_hit": False, "stop_hit": False}
    return {"available": True, "mfe_percent": str((max(bar.high for bar in bars) - trigger) / trigger * 100),
            "mae_percent": str((min(bar.low for bar in bars) - trigger) / trigger * 100),
            "target_8_hit": any(bar.high >= trigger * Decimal("1.08") for bar in bars),
            "stop_hit": any(bar.low <= stop for bar in bars)}


def _continuation_after(trigger: Decimal, bars: tuple[HistoricalBar, ...], pct: int) -> dict[str, object]:
    level = trigger * (1 + Decimal(pct) / 100)
    hit = next((index for index, bar in enumerate(bars) if bar.high >= level), None)
    if hit is None:
        return {"target_hit": False, "max_after_target_percent": None, "pullback_after_target_percent": None}
    after = bars[hit:]
    peak = max(bar.high for bar in after)
    trough = min(bar.low for bar in after)
    return {"target_hit": True, "max_after_target_percent": str((peak - trigger) / trigger * 100),
            "pullback_after_target_percent": str((peak - trough) / peak * 100)}


def _event_label(target: Decimal, stop: Decimal, cutoff: datetime,
                 future: tuple[HistoricalBar, ...]) -> dict[str, object]:
    target_hit = next((bar for bar in future if bar.high >= target), None)
    stop_hit = next((bar for bar in future if bar.low <= stop), None)
    hit = target_hit is not None
    first_target = None if target_hit is None else target_hit.timestamp.isoformat()
    stop_time = None if stop_hit is None else stop_hit.timestamp.isoformat()
    if target_hit is not None and stop_hit is not None:
        if target_hit.timestamp < stop_hit.timestamp:
            event = "TARGET_FIRST"
        elif stop_hit.timestamp < target_hit.timestamp:
            event = "STOP_FIRST"
        else:
            event = "INTRABAR_ORDER_UNKNOWN"
    elif target_hit is not None:
        event = "TARGET_FIRST"
    elif stop_hit is not None:
        event = "STOP_FIRST"
    else:
        event = "CENSORED"
    elapsed = None if target_hit is None else int((target_hit.timestamp - cutoff).total_seconds())
    return {"hit": hit, "first_hit_timestamp": first_target, "elapsed_seconds": elapsed,
            "elapsed_bars": None if elapsed is None else elapsed // 60,
            "stop_hit": stop_hit is not None, "stop_hit_timestamp": stop_time,
            "first_plan_event": event}


def _candidate(context: DiscoveryContext, detections: tuple[object, ...], future: tuple[HistoricalBar, ...],
               provider: HistoricalBarProvider, repository_commit: str,
               parent: KnowledgeEpisode | None = None) -> KnowledgeEpisode:
    ordered = tuple(sorted(detections, key=lambda item: (-_quality(item), item.strategy_id)))
    primary = ordered[0]
    trigger = primary.trigger_level
    stop = primary.structural_stop
    if trigger is None:
        raise ValueError("MISSING_TRIGGER")
    if stop is None:
        raise ValueError("MISSING_STOP")
    risk = trigger - stop
    if risk <= 0:
        raise ValueError("NON_POSITIVE_RISK")
    first = context.completed_bars[0]
    anchor = primary.opportunity_anchor
    ident = episode_id(symbol=context.symbol, trading_date=context.session_date.isoformat(),
                       session=context.session, structural_anchor=anchor,
                       setup_start=first.completed_at, trigger_price=str(trigger), structural_stop=str(stop))
    bars = tuple(HistoricalBar(item.symbol, item.completed_at, item.open, item.high, item.low,
                               item.close, item.volume, item.session, provider.source, provider.version,
                               "UTC", 1) for item in context.completed_bars)
    invalidation = tuple(sorted({reason for item in ordered for reason in item.reason_codes}))
    provenance = {"provider": provider.source, "feed": getattr(provider, "feed", None),
                  "data_version": provider.version,
                  "ingested_at": datetime.now(UTC).isoformat(), "source_timezone": "UTC/aware",
                  "repository_commit": repository_commit, "detector_version": primary.strategy_version,
                  "decision_cutoff": context.decision_cutoff.isoformat(), "normalization_version": 1}
    outcomes = _outcomes(trigger, stop, context.decision_cutoff, future)
    reentry = build_reentry_label(parent, ident, context, ordered, trigger, stop, risk)
    return KnowledgeEpisode(
        ident, context.symbol.upper(), context.session_date, context.session.upper(), primary.strategy_id,
        tuple(item.strategy_id for item in ordered), first.completed_at, context.decision_cutoff,
        context.decision_cutoff, trigger, stop, risk, anchor, anchor, invalidation, invalidation,
        context.completed_bars[-1].close, context.percentage_change, None, context.relative_volume,
        None, context.dollar_volume, None, None, context.spread_percent, context.prior_close,
        None if context.prior_close in (None, 0) else (context.completed_bars[0].open - context.prior_close) / context.prior_close * 100,
        None, max((bar.high for bar in context.completed_bars), default=None), None, None, context.vwap,
        bars, provenance, outcomes, reentry,
        feature_snapshot(bars, context.decision_cutoff, previous_close=context.prior_close,
                         trigger_price=trigger, structural_stop=stop,
                         memberships=tuple(item.strategy_id for item in ordered),
                         provider=provider.source, feed=getattr(provider, "feed", None),
                         repository_commit=repository_commit, normalization_version=1,
                         coverage_class="SINGLE_EXCHANGE_FREE_RESEARCH" if getattr(provider, "feed", "").upper() == "IEX" else None),
    )


def build_reentry_label(parent: KnowledgeEpisode | None, new_episode_id: str,
                        context: DiscoveryContext, detections: tuple[object, ...],
                        trigger: Decimal, stop: Decimal, risk: Decimal) -> dict[str, object] | None:
    """Link only a later distinct structural episode to its latest parent."""
    if parent is None or parent.detected_timestamp >= context.decision_cutoff:
        return None
    parent_target = parent.outcomes.get("percent_targets", {}).get("2", {})
    if not parent_target.get("hit", False):
        return None
    parent_mfe = parent.outcomes.get("mfe_per_share")
    parent_peak = parent.trigger_price if parent_mfe is None else parent.trigger_price + Decimal(parent_mfe)
    return {
        "parent_episode_id": parent.episode_id, "new_episode_id": new_episode_id,
        "time_since_parent": int((context.decision_cutoff - parent.detected_timestamp).total_seconds()),
        "price_change_since_parent": str((context.completed_bars[-1].close - parent.trigger_price) / parent.trigger_price * 100),
        "pullback_from_parent_MFE": str((parent_peak - context.completed_bars[-1].close) / parent_peak * 100),
        "new_strategy_memberships": tuple(item.strategy_id for item in detections),
        "new_primary_strategy": detections[0].strategy_id, "new_trigger": str(trigger),
        "new_stop": str(stop), "new_risk_per_share": str(risk),
        "parent_reached_2_percent": True,
    }


def build_corpus(provider: HistoricalBarProvider, output: Path, *, repository_commit: str,
                 maximum_bars: int = 64, partition_id: str | None = None,
                 partition_metadata: dict[str, object] | None = None) -> BuildSummary:
    if maximum_bars <= 0:
        raise ValueError("maximum_bars must be positive")
    store = KnowledgeStore(output)
    source_identity = getattr(provider, "identity", f"{provider.source}|{provider.version}")
    prior_checkpoint = None
    if store.checkpoint_path.exists():
        try:
            prior_checkpoint = json.loads(store.checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prior_checkpoint = None
    if (partition_id is None and prior_checkpoint and prior_checkpoint.get("completed") and
            prior_checkpoint.get("source_identity") == source_identity):
        status = store.status()
        return BuildSummary({}, 0, 0, 0, 0, 0,
                            {strategy: {"accepted_unique": 0}
                             for strategy in ACTIVE_STRATEGIES})
    bars = sorted(provider.bars(), key=lambda item: (item.symbol, item.timestamp))
    grouped: dict[tuple[str, date], list[HistoricalBar]] = defaultdict(list)
    for bar in bars:
        grouped[(bar.symbol, bar.timestamp.astimezone(EASTERN).date())].append(bar)
    raw = Counter(); per = {strategy: Counter() for strategy in ACTIVE_STRATEGIES}
    quarantined = exact = near = accepted_this_run = 0
    for line_number, reason in getattr(provider, "errors", ()):
        quarantined += 1
        store.append_quarantine(QuarantineRecord(
            hashlib.sha256(f"{provider.source}|{line_number}|{reason}".encode()).hexdigest(),
            "UNKNOWN", None, None, None, "MALFORMED_BARS", f"line {line_number}: {reason}",
            {"provider": provider.source, "repository_commit": repository_commit},
        ))
    registry = default_registry()
    references = getattr(provider, "references", lambda: {})()
    latest_by_symbol: dict[str, KnowledgeEpisode] = {}
    for row in store.iter_episodes():
        # Existing rows are only needed for parent identity on a resumed build.
        # Full bar payloads are not retained in this index.
        latest_by_symbol[row["symbol"]] = None  # type: ignore[assignment]
    for (symbol, day), day_bars in sorted(grouped.items()):
        day_bars.sort(key=lambda item: item.timestamp)
        for index in range(len(day_bars)):
            current = day_bars[index]
            cutoff = current.timestamp + timedelta(minutes=1)
            window = day_bars[max(0, index + 1 - maximum_bars):index + 1]
            completed = tuple(_completed(item) for item in window)
            regular = tuple(item for item in completed if item.session.upper() == "REGULAR")
            premarket = tuple(item for item in completed if item.session.upper() == "PREMARKET")
            context = DiscoveryContext(
                symbol, day, current.session, cutoff, completed,
                FeatureCapabilities(
                    completed_bars=bool(completed), impulse_history=len(completed) >= 3,
                    pullback_history=len(completed) >= 3, session_hod=bool(completed),
                    opening_range=len(regular) >= 5, premarket_history=bool(premarket),
                    prior_close=(symbol, day.isoformat()) in references,
                ), prior_close=references.get((symbol, day.isoformat())),
            )
            detections = tuple(item for item in registry.evaluate(context)
                               if item.strategy_id in ACTIVE_STRATEGIES)
            fired = tuple(item for item in detections if item.state in {DetectionState.DETECTED, DetectionState.STRENGTHENING})
            if not fired:
                continue
            groups: dict[str, list[object]] = defaultdict(list)
            for item in fired:
                raw[item.strategy_id] += 1; per[item.strategy_id]["raw_detections"] += 1
                groups[item.opportunity_anchor].append(item)
            future = tuple(item for item in day_bars[index + 1:])
            for anchor, members in groups.items():
                try:
                    candidate = _candidate(context, tuple(members), future, provider, repository_commit,
                                           latest_by_symbol.get(symbol))
                except ValueError as exc:
                    quarantined += 1
                    store.append_quarantine(QuarantineRecord(
                        hashlib.sha256(f"{symbol}|{cutoff.isoformat()}|{anchor}".encode()).hexdigest(),
                        symbol, day.isoformat(), members[0].strategy_id, cutoff.isoformat(), str(exc),
                        "detector output failed the immutable episode quality gate",
                        {"provider": provider.source, "repository_commit": repository_commit},
                    ))
                    continue
                if candidate.episode_id in store.episode_ids:
                    exact += 1
                    for strategy in candidate.strategy_memberships: per[strategy]["exact_duplicates"] += 1
                    continue
                if any(store.has_near(near_key(candidate.symbol, day.isoformat(), candidate.session, strategy, anchor))
                       for strategy in candidate.strategy_memberships):
                    near += 1
                    for strategy in candidate.strategy_memberships: per[strategy]["near_duplicates"] += 1
                    continue
                store.append_episode(candidate)
                accepted_this_run += 1
                latest_by_symbol[symbol] = candidate
                for strategy in candidate.strategy_memberships: per[strategy]["accepted_unique"] += 1
    for strategy in ACTIVE_STRATEGIES:
        per[strategy]["quarantined"] = 0
    summary = BuildSummary(dict(raw), exact, near, quarantined, accepted_this_run,
                           sum(per[strategy]["accepted_unique"] for strategy in ACTIVE_STRATEGIES),
                           {key: dict(value) for key, value in per.items()})
    store.write_manifest({"knowledge_pack": "ATLAS_TRADING_KNOWLEDGE_V1", "episode_schema_version": 1,
                          "labeling_version": 1, "dedupe_version": 1, "active_strategies": list(ACTIVE_STRATEGIES),
                          "target_per_strategy": 5000, "target_memberships": 115000,
                          "repository_commit": repository_commit, "storage": "append-only JSONL",
                          "source": provider.source, "source_version": provider.version,
                          "source_identity": source_identity,
                          "summary": summary.__dict__ if hasattr(summary, "__dict__") else {
                              "accepted_unique": summary.accepted_unique, "strategy_memberships": summary.strategy_memberships,
                              "exact_duplicates": summary.exact_duplicates, "near_duplicates": summary.near_duplicates,
                              "quarantined": summary.quarantined,
                          }})
    if partition_id is not None:
        metadata = dict(partition_metadata or {})
        metadata.update({"mining_partition_id": partition_id, "status": "COMPLETE",
                         "completed_at": datetime.now(UTC).isoformat(),
                         "accepted_unique": summary.accepted_unique,
                         "strategy_memberships": summary.strategy_memberships,
                         "quarantined": summary.quarantined,
                         "exact_duplicates": summary.exact_duplicates,
                         "near_duplicates": summary.near_duplicates,
                         "repository_commit": repository_commit,
                         "source_identity": source_identity})
        store.record_mined_partition(metadata)
        store.write_checkpoint({"completed": False, "mode": "INCREMENTAL_PARTITIONS",
                                "source": provider.source, "source_identity": source_identity,
                                "repository_commit": repository_commit})
    else:
        store.write_checkpoint({"completed": True, "mode": "FULL_BUILD",
                                "source": provider.source, "source_identity": source_identity,
                                "repository_commit": repository_commit})
    aggregate = store.status()
    mined = store.mined_partitions()
    manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    manifest["summary"] = {"accepted_unique": aggregate["unique_episodes"],
                            "strategy_memberships": aggregate["strategy_memberships"],
                            "quarantined": sum(int(row.get("quarantined", 0)) for row in mined.values()),
                            "exact_duplicates": sum(int(row.get("exact_duplicates", 0)) for row in mined.values()),
                            "near_duplicates": sum(int(row.get("near_duplicates", 0)) for row in mined.values())}
    store.write_manifest(manifest)
    return summary

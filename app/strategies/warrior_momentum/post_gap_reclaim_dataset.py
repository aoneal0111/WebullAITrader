"""Read-only forward-capture analysis for POST_GAP_RECLAIM research."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .models import MinuteBar
from .post_gap_reclaim_research import (
    FrozenPlanOutcome,
    PostGapCandidateContext,
    PostGapReclaimDetection,
    PostGapReclaimState,
    PostGapResearchConfig,
    ResearchOutcomeState,
    detect_post_gap_reclaim,
    evaluate_frozen_plan,
)


@dataclass(frozen=True, slots=True)
class CaptureOpportunity:
    symbol: str
    session: str
    gap_start: datetime
    gap_end: datetime
    post_gap_bars: int
    detections: tuple[PostGapReclaimDetection | None, ...]
    outcomes: tuple[FrozenPlanOutcome | None, ...]


def _read_rows(path: Path) -> tuple[dict[str, object], ...]:
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return tuple(
            {"symbol": symbol, "record_type": kind, "timestamp": timestamp, "payload": json.loads(payload)}
            for symbol, kind, timestamp, payload in connection.execute(
                "SELECT symbol,record_type,timestamp,payload_json FROM capture_records ORDER BY sequence"
            )
        )


def _bars_by_symbol(rows: tuple[dict[str, object], ...]) -> dict[str, tuple[MinuteBar, ...]]:
    grouped: dict[str, dict[datetime, MinuteBar]] = {}
    for row in rows:
        if row["record_type"] != "MINUTE_BAR":
            continue
        payload = row["payload"]
        assert isinstance(payload, dict)
        timestamp = datetime.fromisoformat(str(payload["bar_timestamp"]))
        grouped.setdefault(str(row["symbol"]), {})[timestamp] = MinuteBar(
            str(row["symbol"]), timestamp,
            *(Decimal(str(payload[name])) for name in ("open", "high", "low", "close", "volume")),
        )
    return {symbol: tuple(sorted(values.values(), key=lambda bar: bar.timestamp)) for symbol, values in grouped.items()}


def _decision_contexts(rows: tuple[dict[str, object], ...]) -> dict[str, dict[datetime, tuple[str, PostGapCandidateContext]]]:
    contexts: dict[str, dict[datetime, tuple[str, PostGapCandidateContext]]] = {}
    for row in rows:
        if row["record_type"] != "DECISION":
            continue
        payload = row["payload"]
        assert isinstance(payload, dict)
        timestamps = tuple(datetime.fromisoformat(str(value)) for value in payload.get("bar_timestamps", ()))
        if not timestamps:
            continue
        observation = payload.get("observation", {})
        assert isinstance(observation, dict)
        price = Decimal(str(observation.get("price", "0")))
        previous = Decimal(str(observation.get("previous_close", "0")))
        volume = Decimal(str(observation.get("current_volume", "0")))
        average = Decimal(str(observation.get("average_30_day_volume", "0")))
        bid = observation.get("bid")
        ask = observation.get("ask")
        spread = None
        if bid is not None and ask is not None and price > 0:
            spread = (Decimal(str(ask)) - Decimal(str(bid))) / price * Decimal("100")
        features = payload.get("features") or {}
        assert isinstance(features, dict)
        distance = features.get("distance_from_hod_percent")
        context = PostGapCandidateContext(
            momentum_qualified=payload.get("discovery_status") == "PASSED",
            percentage_change=(price - previous) / previous * Decimal("100") if previous > 0 else Decimal("0"),
            relative_volume=volume / average if average > 0 else Decimal("0"),
            dollar_volume=price * volume,
            spread_percent=spread,
            float_shares=None if observation.get("float_shares") is None else Decimal(str(observation["float_shares"])),
            distance_from_hod_percent=None if distance is None else Decimal(str(distance)),
        )
        contexts.setdefault(str(row["symbol"]), {})[timestamps[-1]] = (str(payload.get("session", "UNKNOWN")), context)
    return contexts


def _gap_sequences(bars: tuple[MinuteBar, ...]) -> tuple[tuple[tuple[MinuteBar, ...], tuple[MinuteBar, ...]], ...]:
    result = []
    for index in range(1, len(bars)):
        if bars[index].timestamp - bars[index - 1].timestamp <= timedelta(minutes=1):
            continue
        post_end = len(bars)
        for end in range(index + 1, len(bars)):
            if bars[end].timestamp - bars[end - 1].timestamp > timedelta(minutes=1):
                post_end = end
                break
        result.append((bars[:index], bars[index:post_end]))
    return tuple(result)


def analyze_capture(path: Path, config: PostGapResearchConfig = PostGapResearchConfig()) -> tuple[CaptureOpportunity, ...]:
    rows = _read_rows(path)
    bars = _bars_by_symbol(rows)
    contexts = _decision_contexts(rows)
    opportunities: list[CaptureOpportunity] = []
    for symbol, series in bars.items():
        decisions = contexts.get(symbol, {})
        if not decisions:
            continue
        first_decision = min(decisions)
        for pre_gap, post_gap in _gap_sequences(series):
            if post_gap[0].timestamp < first_decision.replace(second=0, microsecond=0):
                continue
            if len(post_gap) < 3:
                continue
            detections: list[PostGapReclaimDetection | None] = []
            outcomes: list[FrozenPlanOutcome | None] = []
            for count in range(3, 7):
                if len(post_gap) < count:
                    detections.append(None)
                    outcomes.append(None)
                    continue
                context_entry = decisions.get(post_gap[count - 1].timestamp)
                if context_entry is None:
                    detections.append(None)
                    outcomes.append(None)
                    continue
                detection = detect_post_gap_reclaim(pre_gap + post_gap[:count], context_entry[1], config)
                detections.append(detection)
                outcomes.append(evaluate_frozen_plan(detection, post_gap[3:]) if detection.plan else None)
            session = next((entry[0] for timestamp, entry in sorted(decisions.items()) if timestamp >= post_gap[2].timestamp), "UNKNOWN")
            opportunities.append(CaptureOpportunity(symbol, session, pre_gap[-1].timestamp, post_gap[0].timestamp, len(post_gap), tuple(detections), tuple(outcomes)))
    return tuple(opportunities)


__all__ = ["CaptureOpportunity", "analyze_capture"]

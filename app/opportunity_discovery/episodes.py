"""Bounded detector-owned taxonomy episode continuity."""

from __future__ import annotations

from collections import OrderedDict
import base64
import binascii
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .contracts import DetectionState, StrategyDetection


ACTIVE_STATES = frozenset({
    DetectionState.FORMING, DetectionState.TRIGGER_ARMED,
    DetectionState.DETECTED, DetectionState.STRENGTHENING,
})


@dataclass(frozen=True, slots=True)
class TaxonomyEpisodeState:
    context_key: tuple[str, str, str]
    episode_anchor: str
    first_raw_anchor: str
    triggered: bool = False
    recovered_strategies: tuple[str, ...] = ()
    structural_provenance: str = ""
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class TaxonomyEpisodeRecovery:
    symbol: str
    session_date: str
    session: str
    episode_anchor: str
    strategies: tuple[str, ...] = ()
    triggered: bool = False
    trigger_price: Decimal | None = None
    structural_provenance: str = ""


class TaxonomyEpisodeTracker:
    """Own stable taxonomy episode identity and completed-bar trigger latches.

    State is bounded by ``maximum_contexts``.  A context is a symbol/date/session
    tuple; all strategy memberships observed in that context share its episode.
    Recomputed detector geometry is deliberately not identity material.  A
    context is reset only when no detector reports an active structure; a
    session change naturally uses a different context key.
    """

    def __init__(self, *, maximum_contexts: int = 1_000,
                 recovery: Iterable[TaxonomyEpisodeRecovery] = ()) -> None:
        if maximum_contexts <= 0:
            raise ValueError("taxonomy episode capacity must be positive")
        self.maximum_contexts = maximum_contexts
        self._episodes: OrderedDict[tuple[str, str, str], TaxonomyEpisodeState] = OrderedDict()
        self._strategies: OrderedDict[
            tuple[tuple[str, str, str], str], tuple[DetectionState, object, object]
        ] = OrderedDict()
        self.restore(recovery)

    def restore(self, records: Iterable[TaxonomyEpisodeRecovery]) -> None:
        """Restore only bounded, durable active-episode evidence at startup."""
        for record in records:
            key = (record.symbol.strip().upper(), str(record.session_date), record.session.strip().upper())
            if not all(key) or not record.episode_anchor.strip():
                continue
            self._episodes[key] = TaxonomyEpisodeState(
                key, record.episode_anchor, record.episode_anchor,
                bool(record.triggered), tuple(record.strategies), record.structural_provenance, True,
            )
            for strategy in record.strategies:
                self._strategies[(key, str(strategy))] = (
                    DetectionState.DETECTED if record.triggered else DetectionState.TRIGGER_ARMED,
                    record.trigger_price, None,
                )
            self._episodes.move_to_end(key)
        self._evict()

    def stabilize(self, detections: tuple[StrategyDetection, ...]) -> tuple[StrategyDetection, ...]:
        grouped: dict[tuple[str, str, str], list[StrategyDetection]] = {}
        for item in detections:
            key = (item.symbol.upper(), item.session_date.isoformat(), item.session.upper())
            grouped.setdefault(key, []).append(item)

        for key, rows in grouped.items():
            active = [item for item in rows if item.state in ACTIVE_STATES]
            if not active:
                self._reset(key)
                continue
            prior = self._episodes.get(key)
            provenance = {item.structural_provenance for item in active if item.structural_provenance}
            if prior is not None and prior.recovered and prior.structural_provenance and (
                    not provenance or prior.structural_provenance not in provenance):
                self._reset(key)
                prior = None
            if (prior is not None and prior.recovered and prior.recovered_strategies and
                    not set(prior.recovered_strategies) & {item.strategy_id for item in active}):
                self._reset(key)
                prior = None
            if prior is None:
                raw = min(item.opportunity_anchor for item in active)
                stable = min(provenance) if provenance else "legacy|" + raw
                token = sha256(("ATLAS_TAXONOMY_EPISODE_V2|" + "|".join(key) + "|" + stable).encode()).hexdigest()
                encoded = base64.urlsafe_b64encode(stable.encode()).decode().rstrip("=")
                prior = TaxonomyEpisodeState(
                    key, f"TAXONOMY_EPISODE|{encoded}|{token}", raw,
                    structural_provenance=stable,
                )
            triggered = prior.triggered or any(item.state is DetectionState.DETECTED for item in active)
            prior = replace(prior, triggered=triggered, recovered=False)
            self._episodes[key] = prior
            self._episodes.move_to_end(key)
            self._evict()

        result: list[StrategyDetection] = []
        for item in detections:
            key = (item.symbol.upper(), item.session_date.isoformat(), item.session.upper())
            episode = self._episodes.get(key)
            if episode is None or item.state not in ACTIVE_STATES:
                result.append(item)
                continue
            strategy_key = (key, item.strategy_id)
            prior_row = self._strategies.get(strategy_key)
            previous = None if prior_row is None else prior_row[0]
            latched = previous is DetectionState.DETECTED
            state = DetectionState.DETECTED if latched else item.state
            trigger = item.trigger_level
            if previous is DetectionState.DETECTED and prior_row is not None:
                # Current valid detector geometry wins; durable provenance is
                # the fallback needed after restart when geometry is absent.
                trigger = item.trigger_level if item.trigger_level is not None else prior_row[1]
            self._strategies[strategy_key] = (state, trigger, item.structural_stop)
            self._strategies.move_to_end(strategy_key)
            result.append(replace(
                item,
                state=state,
                trigger_level=trigger,
                setup_anchor=f"{episode.episode_anchor}|{item.strategy_id}",
                opportunity_anchor=episode.episode_anchor,
            ))
        self._evict()
        return tuple(result)

    def _reset(self, key: tuple[str, str, str]) -> None:
        self._episodes.pop(key, None)
        for strategy_key in tuple(self._strategies):
            if strategy_key[0] == key:
                self._strategies.pop(strategy_key, None)

    def reset(self, symbol: str, session_date: str, session: str) -> None:
        """Explicitly invalidate one detector-owned taxonomy context."""
        self._reset((symbol.strip().upper(), str(session_date), session.strip().upper()))

    def _evict(self) -> None:
        while len(self._episodes) > self.maximum_contexts:
            key, _ = self._episodes.popitem(last=False)
            for strategy_key in tuple(self._strategies):
                if strategy_key[0] == key:
                    self._strategies.pop(strategy_key, None)

    def memory_metrics(self) -> dict[str, int]:
        return {"context_count": len(self._episodes), "strategy_count": len(self._strategies)}


def load_taxonomy_episode_recovery(path: str | Path, *, maximum_records: int = 1_000) -> tuple[TaxonomyEpisodeRecovery, ...]:
    """Load recent canonical taxonomy evidence once; never used on the hot path."""
    if maximum_records <= 0 or not Path(path).is_file():
        return ()
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT t.opportunity_id,t.event_type,t.source,t.observed_at,t.payload_json,"
            "t.trigger_price,s.symbol,s.last_stage "
            "FROM opportunity_timeline t JOIN opportunity_state s USING(opportunity_id) "
            "WHERE t.source IN ('TAXONOMY','BOTH') "
            "AND t.event_type IN ('TRIGGER_READY','TRIGGERED') "
            "AND NOT EXISTS (SELECT 1 FROM opportunity_timeline terminal "
            "WHERE terminal.opportunity_id=t.opportunity_id "
            "AND terminal.event_type IN ('INVALIDATED','CONSUMED','ORDER_SUBMITTED','FILLED')) "
            "ORDER BY t.observed_at DESC,t.event_id DESC LIMIT ?", (maximum_records,)
        ).fetchall()
    except sqlite3.Error:
        return ()
    finally:
        if 'connection' in locals():
            connection.close()
    latest: dict[str, TaxonomyEpisodeRecovery] = {}
    for row in rows:
        if row[0] in latest or row[7] not in {"TRIGGER_READY", "TRIGGERED", "POST_TRIGGER_EXTENDED"}:
            continue
        try:
            payload = json.loads(row[4])
            body = payload.get("payload", payload)
            anchor = str(body.get("structural_anchor", ""))
            if not anchor.startswith("TAXONOMY_EPISODE|"):
                continue
            session = str(body.get("session", ""))
            session_date = str(body.get("session_date", ""))
            if not session or not session_date:
                parts = anchor.split("|")
                if len(parts) >= 4 and parts[0] != "TAXONOMY_EPISODE":
                    session_date, session = parts[1], parts[2]
            if not session_date or not session:
                session_date = str(row[3])[:10]
                session = "REGULAR"
            latest[row[0]] = TaxonomyEpisodeRecovery(
                str(row[6]), session_date, session, anchor,
                (str(payload.get("strategy")),) if payload.get("strategy") else (),
                row[1] == "TRIGGERED",
                _decimal(row[5]),
                _provenance_from_anchor(anchor),
            )
            if not latest[row[0]].structural_provenance:
                latest.pop(row[0])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return tuple(latest.values())


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None


def _provenance_from_anchor(anchor: str) -> str:
    parts = anchor.split("|")
    if len(parts) != 3 or parts[0] != "TAXONOMY_EPISODE":
        return ""
    try:
        padding = "=" * (-len(parts[1]) % 4)
        value = base64.urlsafe_b64decode(parts[1] + padding).decode()
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return ""
    return value if value.startswith("taxonomy-structural-provenance-v1|") else ""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

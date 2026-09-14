"""Observation-only runtime lookup over the DI-1 SQLite artifact."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any

from app.opportunity_discovery import MultiStrategyDiscoveryEngine
from app.trade_intelligence.taxonomy_paper_bridge import _discovery_context

from .cache import LRUCache
from .models import (
    ARTIFACT_VERSION, DI2_VERSION, HistoricalIntelligenceResult,
)


_DEFAULT_ARTIFACT = Path(
    "data/research/historical_tranches/2026_06_01__2026_08_31/"
    "decision_intelligence/historical_decision_intelligence.sqlite3"
)
_LIMITATIONS = (
    "IEX_SINGLE_EXCHANGE", "NO_HISTORICAL_CATALYST_NEWS", "NO_NBBO",
    "NO_SPREAD", "NO_LEVEL2", "BAR_VOLUME_CAPACITY_PROXY_ONLY",
    "PREMARKET_BENCHMARK_LIMITATION", "CONTEXT_MISSINGNESS",
)


class HistoricalDecisionIntelligence:
    """Read-only, fail-closed evidence observer; it owns no production policy."""

    def __init__(self, artifact_path: str | Path = _DEFAULT_ARTIFACT,
                 *, journal_path: str | Path | None = None,
                 cache_capacity: int = 512) -> None:
        self.artifact_path = Path(artifact_path)
        self.journal_path = Path(journal_path) if journal_path else Path(
            "data/decision_intelligence/runtime_observations.sqlite3"
        )
        self._connection: sqlite3.Connection | None = None
        self._valid = False
        self._reason: str | None = None
        self._lock = RLock()
        self._cache = LRUCache[HistoricalIntelligenceResult](cache_capacity)
        self._strategy_cache = LRUCache[dict[str, object]](cache_capacity)
        self._discovery = MultiStrategyDiscoveryEngine()
        self._last_by_opportunity: dict[str, str] = {}
        self._first_seen: dict[str, datetime] = {}
        self._lifecycle_opportunities: dict[str, str] = {}
        self._journal: sqlite3.Connection | None = None

    @property
    def available(self) -> bool:
        return self._valid

    def start(self, environment: str | None = None) -> None:
        if str(environment or "PAPER").strip().upper() not in {"PAPER", "TEST", "SANDBOX"}:
            return
        with self._lock:
            if self._connection is not None or self._valid:
                return
            try:
                if not self.artifact_path.is_file():
                    raise FileNotFoundError(str(self.artifact_path))
                uri = f"file:{self.artifact_path.resolve().as_posix()}?mode=ro"
                connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
                metadata = dict(connection.execute(
                    "SELECT key,value FROM artifact_metadata"))
                if metadata.get("artifact_schema_version") != ARTIFACT_VERSION:
                    raise ValueError("wrong historical artifact version")
                source_report = metadata.get("source_report_path")
                expected_hash = metadata.get("source_report_sha256")
                if source_report and expected_hash and Path(source_report).is_file():
                    if hashlib.sha256(Path(source_report).read_bytes()).hexdigest() != expected_hash:
                        raise ValueError("historical artifact source report is stale")
                required = {"strategy_evidence", "context_evidence", "failure_evidence", "limitations"}
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if not required <= tables:
                    raise ValueError("historical artifact is incomplete")
                self._connection = connection
                self._valid = True
                self._reason = None
                if self.journal_path is not None:
                    self._journal = self._open_journal(self.journal_path)
                    self._restore_discovery_lifecycle()
            except Exception as exc:
                if 'connection' in locals():
                    connection.close()
                self._reason = f"{type(exc).__name__}: {exc}"
                self._valid = False

    def close(self, *, timeout_seconds: float = 5.0) -> bool:
        del timeout_seconds
        with self._lock:
            for connection_name in ("_journal", "_connection"):
                connection = getattr(self, connection_name)
                if connection is not None:
                    try:
                        connection.close()
                    finally:
                        setattr(self, connection_name, None)
            self._valid = False
        return True

    def observe_decision(self, *, value: object, candidate: object,
                         signal: object | None = None,
                         taxonomy_candidate: object | None = None,
                         legacy_candidate: object | None = None) -> HistoricalIntelligenceResult | None:
        """Build and journal evidence. All errors are contained by design."""
        try:
            result = self.evaluate(value=value, candidate=candidate, signal=signal,
                                   taxonomy_candidate=taxonomy_candidate)
            if result.opportunity_id is not None:
                self._record_decision_events(
                    result, candidate=candidate, legacy_candidate=legacy_candidate,
                    taxonomy_candidate=taxonomy_candidate, signal=signal,
                )
                self._journal_result(result)
            return result
        except Exception as exc:
            self._reason = f"{type(exc).__name__}: {exc}"
            return None

    def evaluate(self, *, value: object, candidate: object,
                 signal: object | None = None,
                 taxonomy_candidate: object | None = None) -> HistoricalIntelligenceResult:
        if not self._valid or self._connection is None:
            return HistoricalIntelligenceResult(
                artifact_version=ARTIFACT_VERSION,
                entry_assessment="NO_HISTORICAL_INTELLIGENCE",
                fallback_reason=self._reason or "not_started",
            )
        observation = value.observation
        setup = getattr(candidate, "setup", None)
        symbol = str(observation.symbol).strip().upper()
        timestamp = getattr(value, "evaluation_timestamp", None) or observation.timestamp
        trigger = getattr(setup, "trigger", None)
        stop = getattr(setup, "stop_price", None)
        price = observation.price
        discovered = self._discover_memberships(value)
        memberships, discovered_anchor, discovered_state = discovered[:3]
        armed_memberships = discovered[3] if len(discovered) > 3 else ()
        if setup is not None and getattr(setup, "taxonomy_strategy_memberships", ()):
            memberships = tuple(dict.fromkeys((*memberships, *setup.taxonomy_strategy_memberships)))
        primary = memberships[0] if memberships else (
            None if setup is None else getattr(setup, "setup_type", None).value
        )
        opportunity_id = (
            None if setup is None else getattr(setup, "taxonomy_opportunity_id", None)
        ) or self._stable_opportunity(symbol, timestamp, memberships, trigger, discovered_anchor)
        prior_state = self.opportunity_state(opportunity_id)
        first_seen = self._first_seen.setdefault(
            opportunity_id,
            _parse_time(None if prior_state is None else prior_state.get("first_recognized_at"))
            or timestamp,
        )
        setup_state = (
            "NO_SETUP" if setup is None and not discovered_state
            else discovered_state if setup is None or discovered_state == "TRIGGER_ARMED"
            else str(setup.state.value)
        )
        # Recover the detector lifecycle boundary after a process restart.
        if setup_state == "TRIGGER_ARMED" and prior_state is not None and prior_state.get("triggered_at"):
            setup_state = "POST_TRIGGER_EXTENDED"
        stage = _stage(setup_state, trigger, price)
        location, assessment = _location(trigger, price, stop)
        evidence = tuple(self._strategy_row(item) for item in memberships)
        context = tuple(self._context_rows(primary)) if primary else ()
        failures = tuple(self._failure_rows(primary)) if primary else ()
        confidence = _confidence(evidence)
        legacy_first = _parse_time(None if prior_state is None else prior_state.get("legacy_first_seen"))
        taxonomy_first = _parse_time(None if prior_state is None else prior_state.get("taxonomy_first_seen"))
        recognition_source = "TAXONOMY" if memberships else "LEGACY_WARRIOR" if setup is not None else "UNKNOWN"
        if legacy_first and taxonomy_first:
            recognition_source = "BOTH"
        first_price = _parse_decimal(None if prior_state is None else prior_state.get("first_recognized_price")) or price
        recognition_timing = _recognition_timing(first_price, trigger)
        waiting = _waiting_classification(first_price, price, trigger, signal is not None)
        root = "SAW_EARLY_BUT_WAITED" if recognition_timing == "EARLY" and waiting == "LATE_DECISION_AFTER_EARLY_RECOGNITION" else (
            "RECOGNIZED_LATE" if recognition_timing in {"RECOGNIZED_AFTER_TRIGGER", "RECOGNIZED_EXTENDED"} else "INSUFFICIENT_DATA"
        )
        signature = hashlib.sha256(json.dumps({
            "memberships": memberships, "stage": stage, "location": location,
            "confidence": confidence, "trigger": _json_value(trigger),
            "price": _json_value(price),
        }, sort_keys=True).encode()).hexdigest()
        result = HistoricalIntelligenceResult(
            recognized_memberships=memberships, primary_strategy=primary,
            membership_signature="|".join(memberships), opportunity_id=opportunity_id,
            trading_date=observation.timestamp.date().isoformat(),
            structural_anchor=discovered_anchor,
            session=getattr(value, "session", None), first_recognized_at=first_seen,
            opportunity_age_seconds=Decimal(str((timestamp - first_seen).total_seconds())),
            recognition_timing=recognition_timing,
            waiting_classification=waiting, root_diagnostic=root,
            recognition_source=recognition_source,
            legacy_first_seen=legacy_first,
            legacy_first_price=_parse_decimal(None if prior_state is None else prior_state.get("legacy_first_price")),
            taxonomy_first_seen=taxonomy_first,
            taxonomy_first_price=_parse_decimal(None if prior_state is None else prior_state.get("taxonomy_first_price")),
            entry_decision_observed=signal is not None,
            setup_state=setup_state, setup_stage=stage, entry_location=location,
            entry_assessment=assessment, trigger_price=trigger,
            structural_stop=stop, reference_price=getattr(candidate, "price", None),
            current_price=price,
            gap_percent=(None if observation.previous_close <= 0 else
                         (price / observation.previous_close - 1) * 100),
            relative_volume=(None if observation.average_30_day_volume <= 0 else
                             observation.current_volume / observation.average_30_day_volume),
            distance_from_hod_percent=getattr(candidate, "distance_from_hod_percent", None),
            distance_to_trigger=None if trigger is None or price is None else trigger - price,
            distance_to_trigger_percent=(None if trigger is None or price in (None, 0)
                                         else (trigger - price) / price * 100),
            price_beyond_trigger=None if trigger is None or price is None else price - trigger,
            price_beyond_trigger_percent=(None if trigger is None or price in (None, 0)
                                          else (price - trigger) / price * 100),
            setup_evidence=evidence, context_evidence=context,
            readiness_memberships=tuple(armed_memberships),
            failure_evidence=failures, confidence=confidence,
            coverage="MATCHED" if evidence else "MISSING_CONTEXT",
            limitations=_LIMITATIONS, evaluated_at=timestamp,
        )
        self._cache.put(opportunity_id, result)
        return result

    def record_event(self, *, opportunity_id: str, event_type: str,
                     observed_at: datetime, symbol: str, source: str = "UNKNOWN",
                     strategy: str | None = None, price: Decimal | None = None,
                     trigger_price: Decimal | None = None, order_id: str | None = None,
                     fill_id: str | None = None, quantity: Decimal | None = None,
                     payload: dict[str, object] | None = None) -> None:
        """Persist an already-observed PAPER lifecycle event; never emits one."""
        if self._journal is None:
            return
        try:
            self._record_event(
                opportunity_id=opportunity_id, event_type=event_type, source=source,
                strategy=strategy, observed_at=observed_at, symbol=symbol,
                price=price, trigger_price=trigger_price, order_id=order_id,
                fill_id=fill_id, quantity=quantity, payload=payload or {},
            )
        except Exception as exc:
            self._reason = f"{type(exc).__name__}: {exc}"

    def timeline(self, opportunity_id: str) -> tuple[dict[str, object], ...]:
        if self._journal is None:
            return ()
        rows = self._journal.execute(
            "SELECT event_type,source,strategy,observed_at,price,trigger_price,order_id,fill_id,quantity,payload_json "
            "FROM opportunity_timeline WHERE opportunity_id=? ORDER BY observed_at,event_id",
            (opportunity_id,),
        ).fetchall()
        names = ("event_type", "source", "strategy", "observed_at", "price",
                 "trigger_price", "order_id", "fill_id", "quantity", "payload_json")
        return tuple(dict(zip(names, row)) for row in rows)

    def _record_decision_events(self, result: HistoricalIntelligenceResult, *,
                                candidate: object, legacy_candidate: object | None,
                                taxonomy_candidate: object | None,
                                signal: object | None) -> None:
        symbol = str(getattr(candidate, "symbol", ""))
        setup = getattr(candidate, "setup", None)
        sources = []
        if legacy_candidate is not None and getattr(legacy_candidate, "setup", None) is not None:
            sources.append("LEGACY_WARRIOR")
        if taxonomy_candidate is not None or result.recognized_memberships:
            sources.append("TAXONOMY")
        if not sources and setup is not None:
            sources.append("LEGACY_WARRIOR")
        for source in tuple(dict.fromkeys(sources)):
            for strategy in result.recognized_memberships or (result.primary_strategy,):
                if strategy:
                    self._record_event(
                        opportunity_id=result.opportunity_id, event_type="FIRST_RECOGNIZED",
                        source=source, strategy=strategy, observed_at=result.evaluated_at,
                        symbol=symbol, price=result.current_price,
                        trigger_price=result.trigger_price, payload={"stage": result.setup_stage},
                    )
        if result.setup_stage in {"STRUCTURE_PRESENT", "NEAR_TRIGGER", "TRIGGER_READY", "TRIGGERED", "POST_TRIGGER_EXTENDED"}:
            self._record_event(
                opportunity_id=result.opportunity_id, event_type=_event_for_stage(result.setup_stage),
                source="BOTH" if len(sources) > 1 else (sources[0] if sources else "UNKNOWN"),
                strategy=result.primary_strategy, observed_at=result.evaluated_at,
                symbol=symbol, price=result.current_price,
                trigger_price=result.trigger_price, payload={
                    "entry_location": result.entry_location,
                    "structural_anchor": result.structural_anchor,
                },
            )
        if signal is not None:
            lifecycle = _signal_lifecycle(signal)
            if lifecycle:
                self._lifecycle_opportunities[str(lifecycle)] = result.opportunity_id
            self._record_event(
                opportunity_id=result.opportunity_id, event_type="ENTRY_DECISION",
                source="BOTH" if len(sources) > 1 else (sources[0] if sources else "UNKNOWN"),
                strategy=result.primary_strategy, observed_at=result.evaluated_at,
                symbol=symbol, price=result.current_price,
                trigger_price=result.trigger_price,
                payload={"entry_location": result.entry_location, "artifact_version": ARTIFACT_VERSION},
            )

    def observe_paper_event(self, event: object) -> None:
        """Observe an existing PAPER lifecycle event without owning it."""
        try:
            order = getattr(event, "order", None)
            fill = getattr(event, "fill", None)
            request = getattr(order, "request", None)
            lifecycle = None if request is None else getattr(request, "strategy_lifecycle_id", None)
            lifecycle = lifecycle or getattr(order, "lifecycle_id", None)
            metadata = {} if request is None else getattr(request, "metadata", {})
            opportunity = metadata.get("opportunity_id") if isinstance(metadata, dict) else None
            opportunity = opportunity or self._lifecycle_opportunities.get(str(lifecycle))
            if not opportunity:
                return
            raw_type = str(getattr(event, "event_type", ""))
            event_type = {
                "ORDER_SUBMITTED": "ORDER_SUBMITTED",
                "ORDER_REPLACED": "ORDER_REPLACED",
                "ORDER_CANCELLED": "ORDER_CANCELLED",
                "ORDER_EXPIRED": "ORDER_EXPIRED",
                "ORDER_FILLED": "FILLED",
                "ORDER_PARTIALLY_FILLED": "FILLED",
            }.get(raw_type)
            if event_type is None:
                return
            self.record_event(
                opportunity_id=str(opportunity), event_type=event_type,
                observed_at=getattr(event, "timestamp"),
                symbol=str(getattr(event, "symbol", "")), source="PAPER",
                strategy=(str(getattr(request, "strategy_id", "") or "") if request is not None else None),
                price=(getattr(fill, "fill_price", None) if fill is not None
                       else getattr(request, "limit_price", None) if request is not None else
                       getattr(order, "limit_price", None)),
                order_id=(None if order is None else getattr(order, "order_id", None)),
                fill_id=(None if fill is None else getattr(fill, "request_id", None)),
                quantity=(getattr(fill, "quantity", None) if fill is not None else None),
                payload={"lifecycle_id": lifecycle, "paper_event": raw_type},
            )
        except Exception as exc:
            self._reason = f"{type(exc).__name__}: {exc}"

    def _record_event(self, *, opportunity_id: str, event_type: str, source: str,
                      strategy: str | None, observed_at: datetime | None,
                      symbol: str, price: Decimal | None,
                      trigger_price: Decimal | None, order_id: str | None = None,
                      fill_id: str | None = None, quantity: Decimal | None = None,
                      payload: dict[str, object]) -> None:
        if self._journal is None or observed_at is None:
            return
        body = {"event_type": event_type, "source": source, "strategy": strategy,
                "observed_at": observed_at.isoformat(), "price": _json_value(price),
                "trigger_price": _json_value(trigger_price), "order_id": order_id,
                "fill_id": fill_id, "quantity": _json_value(quantity), "payload": payload}
        event_id = hashlib.sha256(
            f"{opportunity_id}|{event_type}|{source}|{strategy}|{order_id}|{fill_id}".encode()
        ).hexdigest()
        self._journal.execute(
            "INSERT OR IGNORE INTO opportunity_timeline VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, opportunity_id, event_type, source, strategy,
             observed_at.isoformat(), _json_value(price), _json_value(trigger_price),
             order_id, fill_id, _json_value(quantity), json.dumps(body, sort_keys=True)),
        )
        self._journal.commit()
        self._upsert_state(opportunity_id, symbol, event_type, source, observed_at, price)
        if event_type == "TRIGGERED":
            anchor = payload.get("structural_anchor")
            if anchor:
                self._journal.execute(
                    "INSERT OR REPLACE INTO triggered_structural_episodes "
                    "(anchor, opportunity_id, symbol, triggered_at) VALUES (?,?,?,?)",
                    (str(anchor), opportunity_id, symbol, observed_at.isoformat()),
                )
                self._journal.commit()

    def _upsert_state(self, opportunity_id: str, symbol: str, event_type: str,
                      source: str, observed_at: datetime, price: Decimal | None) -> None:
        row = self._journal.execute(
            "SELECT first_recognized_at,first_recognized_price,legacy_first_seen,legacy_first_price,"
            "taxonomy_first_seen,taxonomy_first_price,structure_present_at,structure_present_price,"
            "near_trigger_at,near_trigger_price,trigger_ready_at,trigger_ready_price,triggered_at,triggered_price,"
            "entry_decision_at,entry_decision_price,recognition_source FROM opportunity_state WHERE opportunity_id=?",
            (opportunity_id,),
        ).fetchone()
        values = list(row or (None,) * 17)
        stamp, raw_price = observed_at.isoformat(), _json_value(price)
        positions = {
            "FIRST_RECOGNIZED": (0, 1), "STRUCTURE_PRESENT": (6, 7),
            "NEAR_TRIGGER": (8, 9), "TRIGGER_READY": (10, 11),
            "TRIGGERED": (12, 13), "ENTRY_DECISION": (14, 15),
        }
        if event_type in positions and values[positions[event_type][0]] is None:
            values[positions[event_type][0]] = stamp
            values[positions[event_type][1]] = raw_price
        if event_type == "FIRST_RECOGNIZED":
            if source in {"LEGACY_WARRIOR", "BOTH"} and values[2] is None:
                values[2], values[3] = stamp, raw_price
            if source in {"TAXONOMY", "BOTH"} and values[4] is None:
                values[4], values[5] = stamp, raw_price
        if event_type == "FIRST_RECOGNIZED" and (values[0] is None or stamp < values[0]):
            values[0], values[1] = stamp, raw_price
        if source in {"LEGACY_WARRIOR", "TAXONOMY", "BOTH"}:
            sources = {str(values[16])} if values[16] in {"LEGACY_WARRIOR", "TAXONOMY", "BOTH"} else set()
            sources.add(source)
            values[16] = "BOTH" if {"LEGACY_WARRIOR", "TAXONOMY"} <= sources else next(iter(sources))
        sql = "INSERT INTO opportunity_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) " \
              "ON CONFLICT(opportunity_id) DO UPDATE SET symbol=excluded.symbol, " \
              "first_recognized_at=excluded.first_recognized_at,first_recognized_price=excluded.first_recognized_price, " \
              "legacy_first_seen=excluded.legacy_first_seen,legacy_first_price=excluded.legacy_first_price, " \
              "taxonomy_first_seen=excluded.taxonomy_first_seen,taxonomy_first_price=excluded.taxonomy_first_price, " \
              "structure_present_at=excluded.structure_present_at,structure_present_price=excluded.structure_present_price, " \
              "near_trigger_at=excluded.near_trigger_at,near_trigger_price=excluded.near_trigger_price, " \
              "trigger_ready_at=excluded.trigger_ready_at,trigger_ready_price=excluded.trigger_ready_price, " \
              "triggered_at=excluded.triggered_at,triggered_price=excluded.triggered_price, " \
              "entry_decision_at=excluded.entry_decision_at,entry_decision_price=excluded.entry_decision_price, " \
              "recognition_source=excluded.recognition_source,last_stage=excluded.last_stage,updated_at=excluded.updated_at"
        self._journal.execute(sql, (opportunity_id, symbol, *values, event_type, stamp))
        self._journal.commit()

    def opportunity_state(self, opportunity_id: str) -> dict[str, object] | None:
        if self._journal is None:
            return None
        row = self._journal.execute(
            "SELECT * FROM opportunity_state WHERE opportunity_id=?",
            (opportunity_id,),
        ).fetchone()
        if row is None:
            return None
        names = ("opportunity_id", "symbol", "first_recognized_at", "first_recognized_price",
                 "legacy_first_seen", "legacy_first_price", "taxonomy_first_seen",
                 "taxonomy_first_price", "structure_present_at", "structure_present_price",
                 "near_trigger_at", "near_trigger_price", "trigger_ready_at",
                 "trigger_ready_price", "triggered_at", "triggered_price",
                 "entry_decision_at", "entry_decision_price", "recognition_source",
                 "last_stage", "updated_at")
        return dict(zip(names, row))

    def _restore_discovery_lifecycle(self) -> None:
        if self._journal is None:
            return
        rows = self._journal.execute(
            "SELECT anchor FROM triggered_structural_episodes "
            "ORDER BY triggered_at DESC LIMIT ?",
            (self._discovery.maximum_opportunities,),
        ).fetchall()
        self._discovery.restore_triggered_anchors(tuple(str(row[0]) for row in rows))

    def diagnose_opportunity(self, opportunity_id: str) -> dict[str, object] | None:
        """Return durable, descriptive timing diagnostics for one opportunity."""
        state = self.opportunity_state(opportunity_id)
        if state is None:
            return None
        rows = self.timeline(opportunity_id)
        first_price = _parse_decimal(state.get("first_recognized_price"))
        trigger_row = next((row for row in rows if row["event_type"] == "TRIGGERED"), None)
        trigger = _parse_decimal(None if trigger_row is None else trigger_row["trigger_price"])
        trigger_event_price = _parse_decimal(None if trigger_row is None else trigger_row["price"])
        trigger_ready_row = next((row for row in rows if row["event_type"] == "TRIGGER_READY"), None)
        trigger_ready_price = _parse_decimal(
            None if trigger_ready_row is None else trigger_ready_row["price"]
        )
        decision = next((row for row in rows if row["event_type"] == "ENTRY_DECISION"), None)
        decision_price = _parse_decimal(None if decision is None else decision["price"])
        order = next((row for row in rows if row["event_type"] == "ORDER_SUBMITTED"), None)
        order_price = _parse_decimal(None if order is None else order["price"])
        fill = next((row for row in rows if row["event_type"] == "FILLED"), None)
        fill_price = _parse_decimal(None if fill is None else fill["price"])
        recognition = _recognition_timing(first_price, trigger)
        waiting = _waiting_classification(first_price, decision_price, trigger, decision is not None)
        legacy = _parse_time(state.get("legacy_first_seen"))
        taxonomy = _parse_time(state.get("taxonomy_first_seen"))
        legacy_price = _parse_decimal(state.get("legacy_first_price"))
        taxonomy_price = _parse_decimal(state.get("taxonomy_first_price"))
        leader = "UNKNOWN"
        advantage = None
        price_advantage = None
        if legacy and taxonomy:
            leader = "TAXONOMY" if taxonomy < legacy else "LEGACY" if legacy < taxonomy else "TIE"
            advantage = Decimal(str((legacy - taxonomy).total_seconds() * 1000))
            if legacy_price is not None and taxonomy_price is not None and legacy_price > 0:
                price_advantage = (taxonomy_price / legacy_price - 1) * 100
        elif legacy:
            leader = "ONLY_LEGACY"
        elif taxonomy:
            leader = "ONLY_TAXONOMY"
        movement = {
            "recognition_to_trigger_pct": _percent_move(first_price, trigger_event_price),
            "recognition_to_trigger_ready_pct": _percent_move(first_price, trigger_ready_price),
            "trigger_to_decision_pct": _percent_move(trigger_event_price, decision_price),
            "trigger_ready_to_decision_pct": _percent_move(trigger_ready_price, decision_price),
            "recognition_to_decision_pct": _percent_move(first_price, decision_price),
            "trigger_to_order_pct": _percent_move(trigger_event_price, order_price),
            "decision_to_order_pct": _percent_move(decision_price, order_price),
            "trigger_to_fill_pct": _percent_move(trigger_event_price, fill_price),
            "trigger_ready_to_fill_pct": _percent_move(trigger_ready_price, fill_price),
            "decision_to_fill_pct": _percent_move(decision_price, fill_price),
            "recognition_to_fill_pct": _percent_move(first_price, fill_price),
        }
        return {
            **state, "recognition_timing": recognition,
            "waiting_classification": waiting,
            "root_diagnostic": (
                "SAW_EARLY_BUT_WAITED" if recognition == "EARLY" and waiting == "LATE_DECISION_AFTER_EARLY_RECOGNITION"
                else "RECOGNIZED_LATE" if recognition in {"RECOGNIZED_AFTER_TRIGGER", "RECOGNIZED_EXTENDED"}
                else "INSUFFICIENT_DATA"
            ),
            "recognition_leader": leader, "time_advantage_ms": advantage,
            "price_advantage_percent": None if price_advantage is None else str(price_advantage),
            "trigger_price": None if trigger is None else str(trigger),
            "decision_price": None if decision_price is None else str(decision_price),
            **movement,
        }

    def _discover_memberships(self, value: object) -> tuple[tuple[str, ...], str | None, str | None, tuple[dict[str, object], ...]]:
        try:
            context, _ = _discovery_context(value)
            batch = self._discovery.observe(context)
            detections = getattr(batch, "lifecycle_detections", ()) or batch.detections
            rows = tuple(item for item in detections
                         if item.state.value not in {"NOT_DETECTED", "UNAVAILABLE"})
            state = None if not rows else (
                "TRIGGER_ARMED" if any(item.state.value == "TRIGGER_ARMED" for item in rows)
                else "FORMING" if any(item.state.value == "FORMING" for item in rows)
                else str(rows[0].state.value)
            )
            readiness = tuple({
                "strategy": item.strategy_id,
                "state": item.state.value,
                "trigger": item.trigger_level,
                "structural_stop": item.structural_stop,
                "opportunity_anchor": item.opportunity_anchor,
                "detector_episode_id": item.detector_episode_id,
                "observed_at": item.decision_cutoff,
            } for item in rows if item.state.value == "TRIGGER_ARMED")
            rows = tuple(sorted(rows, key=lambda item: item.strategy_id))
            return (tuple(dict.fromkeys(item.strategy_id for item in rows)),
                    rows[0].opportunity_anchor if rows else None, state, readiness)
        except Exception:
            return (), None, None, ()

    def _strategy_row(self, strategy: str) -> dict[str, object]:
        cached = self._strategy_cache.get(strategy)
        if cached is not None:
            return cached
        row = self._connection.execute(
            "SELECT strategy, whole_sample_count, train_sample_count, validation_sample_count, "
            "test_sample_count, median_mfe, median_mae, median_max_r, target_5_rate, "
            "target_8_rate, test_target_5_rate, test_target_8_rate, stop_first_rate, "
            "test_stop_first_rate, confidence_state, walk_forward_state, descriptive_tier "
            "FROM strategy_evidence WHERE strategy=?", (strategy,)).fetchone()
        if row is None:
            result = {"strategy": strategy, "state": "MISSING_CONTEXT"}
            self._strategy_cache.put(strategy, result)
            return result
        names = ("strategy", "whole_sample_count", "train_sample_count", "validation_sample_count",
                 "test_sample_count", "median_mfe", "median_mae", "median_max_r", "target_5_rate",
                 "target_8_rate", "test_target_5_rate", "test_target_8_rate", "stop_first_rate",
                 "test_stop_first_rate", "confidence_state", "walk_forward_state", "descriptive_tier")
        result = dict(zip(names, row))
        self._strategy_cache.put(strategy, result)
        return result

    def _context_rows(self, strategy: str) -> list[dict[str, object]]:
        rows = self._connection.execute(
            "SELECT context_dimension, context_bucket, sample_count, test_sample_count, "
            "confidence_state, coverage_percent FROM context_evidence "
            "WHERE strategy IN (?, '__ALL__') LIMIT 64", (strategy,)).fetchall()
        return [dict(zip(("dimension", "bucket", "sample_count", "test_sample_count",
                          "confidence", "coverage_percent"), row)) for row in rows]

    def _failure_rows(self, strategy: str) -> list[dict[str, object]]:
        rows = self._connection.execute(
            "SELECT failure_class, context_bucket, sample_count, rate, confidence_state "
            "FROM failure_evidence WHERE strategy IN (?, '__ALL__') LIMIT 32", (strategy,)).fetchall()
        return [dict(zip(("failure_class", "bucket", "sample_count", "rate", "confidence"), row))
                for row in rows]

    @staticmethod
    def _stable_opportunity(symbol: str, timestamp: datetime, memberships: tuple[str, ...],
                            trigger: Decimal | None, anchor: str | None) -> str:
        # Membership growth and small detector recalculations are observations
        # on the same structural episode, not new opportunities.  The durable
        # detector anchor is authoritative whenever it exists; the fallback is
        # only for contexts that cannot expose one.
        identity = anchor or f"fallback|{memberships}|{trigger}"
        return hashlib.sha256(f"di2|{symbol}|{timestamp.date()}|{identity}".encode()).hexdigest()

    @staticmethod
    def _open_journal(path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.execute("CREATE TABLE IF NOT EXISTS intelligence_observations "
                           "(record_id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, "
                           "observed_at TEXT NOT NULL, payload_json TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS opportunity_timeline "
                           "(event_id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, "
                           "event_type TEXT NOT NULL, source TEXT NOT NULL, strategy TEXT, "
                           "observed_at TEXT NOT NULL, price TEXT, trigger_price TEXT, "
                           "order_id TEXT, fill_id TEXT, quantity TEXT, payload_json TEXT NOT NULL, "
                           "UNIQUE(opportunity_id,event_type,source,strategy,order_id,fill_id))")
        connection.execute("CREATE INDEX IF NOT EXISTS timeline_lookup "
                           "ON opportunity_timeline(opportunity_id,observed_at)")
        connection.execute("CREATE TABLE IF NOT EXISTS opportunity_state "
                           "(opportunity_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, "
                           "first_recognized_at TEXT, first_recognized_price TEXT, "
                           "legacy_first_seen TEXT, legacy_first_price TEXT, "
                           "taxonomy_first_seen TEXT, taxonomy_first_price TEXT, "
                           "structure_present_at TEXT, structure_present_price TEXT, "
                           "near_trigger_at TEXT, near_trigger_price TEXT, "
                           "trigger_ready_at TEXT, trigger_ready_price TEXT, "
                           "triggered_at TEXT, triggered_price TEXT, "
                           "entry_decision_at TEXT, entry_decision_price TEXT, "
                           "recognition_source TEXT, last_stage TEXT, updated_at TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS triggered_structural_episodes "
                           "(anchor TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, "
                           "symbol TEXT NOT NULL, triggered_at TEXT NOT NULL)")
        connection.execute("CREATE INDEX IF NOT EXISTS triggered_episode_time "
                           "ON triggered_structural_episodes(triggered_at)")
        connection.commit()
        return connection

    def _journal_result(self, result: HistoricalIntelligenceResult) -> None:
        if self._journal is None or result.opportunity_id is None:
            return
        payload = asdict(result)
        payload = {key: _json_value(value) for key, value in payload.items()}
        # Deduplicate on the observable state, not on enrichment fields that
        # may be filled by restart recovery after the first identical tick.
        material = {
            "opportunity_id": result.opportunity_id,
            "evaluated_at": _json_value(result.evaluated_at),
            "memberships": result.membership_signature,
            "stage": result.setup_stage,
            "location": result.entry_location,
            "price": _json_value(result.current_price),
            "trigger": _json_value(result.trigger_price),
            "entry_decision": result.entry_decision_observed,
        }
        record_id = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
        if self._last_by_opportunity.get(result.opportunity_id) == record_id:
            return
        self._journal.execute(
            "INSERT OR IGNORE INTO intelligence_observations VALUES (?,?,?,?)",
            (record_id, result.opportunity_id, str(result.evaluated_at),
             json.dumps(payload, sort_keys=True, default=str)),
        )
        self._journal.commit()
        self._last_by_opportunity[result.opportunity_id] = record_id


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _signal_lifecycle(signal: object) -> str:
    taxonomy = getattr(signal, "taxonomy_execution_identity", None)
    if taxonomy:
        return str(taxonomy)
    return "|".join(str(value) for value in (
        getattr(signal, "strategy_id", "warrior_momentum"),
        str(getattr(signal, "symbol", "")).strip().upper(),
        getattr(signal, "timestamp", ""),
        getattr(getattr(signal, "setup_type", None), "value", ""),
        getattr(signal, "entry_trigger", ""), getattr(signal, "stop_price", ""),
    ))


def _stage(state: str, trigger: Decimal | None, price: Decimal | None) -> str:
    if state == "TRIGGER_ARMED":
        # This explicit detector fact means the reference level and structural
        # stop exist while the final crossing condition remains false.
        return "TRIGGER_READY"
    if state == "TRIGGERED":
        return "POST_TRIGGER_EXTENDED" if trigger is not None and price is not None and price > trigger else "TRIGGERED"
    if state == "FORMING":
        return "NEAR_TRIGGER" if trigger is not None and price is not None and trigger >= price and (trigger - price) / price <= Decimal("0.01") else "FORMING"
    if state in {"DETECTED", "STRENGTHENING"}:
        return "STRUCTURE_PRESENT"
    return "NO_SETUP" if state in {"UNKNOWN", "NOT_FORMED"} else state


def _event_for_stage(stage: str) -> str:
    return {
        "STRUCTURE_PRESENT": "STRUCTURE_PRESENT",
        "NEAR_TRIGGER": "NEAR_TRIGGER",
        "TRIGGER_READY": "TRIGGER_READY",
        "TRIGGERED": "TRIGGERED",
        "POST_TRIGGER_EXTENDED": "TRIGGERED",
    }.get(stage, stage)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return None


def _percent_move(earlier: Decimal | None, later: Decimal | None) -> str | None:
    """Return the signed long-side movement, or explicit missingness."""
    if earlier is None or later is None or earlier <= 0:
        return None
    return str((later / earlier - 1) * 100)


def _recognition_timing(price: Decimal | None, trigger: Decimal | None) -> str:
    if price is None or trigger is None or trigger <= 0:
        return "INSUFFICIENT_DATA"
    if price > trigger * Decimal("1.015"):
        return "RECOGNIZED_EXTENDED"
    if price > trigger:
        return "RECOGNIZED_AFTER_TRIGGER"
    if price == trigger:
        return "RECOGNIZED_AT_TRIGGER"
    if trigger - price <= trigger * Decimal("0.01"):
        return "RECOGNIZED_NEAR_TRIGGER"
    return "EARLY"


def _waiting_classification(first: Decimal | None, current: Decimal | None,
                            trigger: Decimal | None, decision: bool) -> str:
    if not decision or first is None or current is None or trigger is None or first <= 0:
        return "INSUFFICIENT_DATA"
    move = (current / first - 1) * 100
    if move > Decimal("1") or current > trigger * Decimal("1.015"):
        return "LATE_DECISION_AFTER_EARLY_RECOGNITION"
    if move > Decimal("0.5"):
        return "MODERATE_WAIT"
    return "PROMPT_DECISION"


def _location(trigger: Decimal | None, price: Decimal | None, stop: Decimal | None) -> tuple[str, str]:
    if trigger is None or price is None or price <= 0:
        return "INSUFFICIENT_DATA", "INSUFFICIENT_EVIDENCE"
    if price < trigger:
        distance = (trigger - price) / price
        return ("APPROACHING_TRIGGER" if distance <= Decimal("0.01") else "PRE_TRIGGER",
                "FAVORABLE_LOCATION" if distance <= Decimal("0.01") else "ACCEPTABLE_LOCATION")
    extension = (price - trigger) / trigger
    if extension <= Decimal("0.005"):
        return "AT_TRIGGER", "FAVORABLE_LOCATION"
    if extension <= Decimal("0.015"):
        return "SLIGHTLY_EXTENDED", "LATE_ENTRY_RISK"
    if extension <= Decimal("0.03"):
        return "MODERATELY_EXTENDED", "CHASE_RISK"
    return "HEAVILY_EXTENDED", "CHASE_RISK"


def _confidence(rows: tuple[dict[str, object], ...]) -> str:
    values = {row.get("confidence_state") for row in rows}
    for candidate in ("STRONG_EVIDENCE", "MODERATE_EVIDENCE", "WEAK_EVIDENCE"):
        if candidate in values:
            return candidate
    return "MISSING_CONTEXT" if not rows else "INSUFFICIENT_SAMPLE"


__all__ = ["HistoricalDecisionIntelligence"]

"""Durable, restart-safe Atlas paper trade experiment journal."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from app.momentum_scanner import ScannerDecision
from app.paper_trade_experiment.models import (
    CandidateRecord,
    ExecutionState,
    HORIZONS_SECONDS,
)


SCHEMA_VERSION = "atlas-paper-experiment-v1"
DEFAULT_STRATEGY_VERSION = "momentum-scanner-v1"
DEFAULT_MODEL_VERSION = "none"
_SAFE_EXECUTION_ENVIRONMENTS = frozenset({"PAPER", "TEST"})
_SECRET_KEY_PARTS = (
    "api_key", "api_secret", "password", "credential", "access_token",
    "refresh_token", "authorization", "account_id",
)


class PaperTradeExperimentJournal:
    """SQLite journal with immutable decision features and mutable labels.

    The class has no broker dependency and cannot submit an order.  Execution
    mutations are accepted only for explicit PAPER/TEST records with live
    trading disabled.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiment_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    trade_id TEXT UNIQUE,
                    symbol TEXT NOT NULL,
                    decision_timestamp TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    execution_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS experiment_candidates_symbol_time
                    ON experiment_candidates(symbol, decision_timestamp);
                CREATE TABLE IF NOT EXISTS experiment_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            existing = connection.execute(
                "SELECT value FROM experiment_metadata WHERE key='schema_version'"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO experiment_metadata(key,value) VALUES(?,?)",
                    ("schema_version", SCHEMA_VERSION),
                )
            elif existing["value"] != SCHEMA_VERSION:
                raise ValueError("unsupported paper experiment journal schema")
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise ValueError("paper experiment journal integrity check failed")

    def record_candidate(
        self,
        decision: ScannerDecision,
        *,
        market_session: str = "UNKNOWN",
        scanner_rank: int | None = None,
        strategy_version: str = DEFAULT_STRATEGY_VERSION,
        model_version: str = DEFAULT_MODEL_VERSION,
        application_commit: str | None = None,
        execution_environment: str = "PAPER",
        recorded_at: datetime | None = None,
    ) -> CandidateRecord:
        if decision.timestamp is None or decision.price is None:
            raise ValueError("complete scanner decision timestamp and price are required")
        environment = _safe_environment(execution_environment)
        candidate_id = _candidate_id(decision, strategy_version)
        features = _decision_features(
            decision,
            market_session=market_session,
            scanner_rank=scanner_rank,
            strategy_version=strategy_version,
            model_version=model_version,
            application_commit=application_commit or _safe_application_commit(),
            execution_environment=environment,
        )
        _assert_no_secrets(features)
        now = (recorded_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        execution = {
            "state": ExecutionState.NOT_EXECUTED.value,
            "paper_trade_executed": False,
            "fill_ids": [],
            "fill_quantity": "0",
        }
        encoded_features = _json(features)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT features_json FROM experiment_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if row is not None:
                if row["features_json"] != encoded_features:
                    raise ValueError("immutable candidate decision snapshot mismatch")
                return self.get(candidate_id)
            connection.execute(
                """INSERT INTO experiment_candidates(
                    candidate_id,trade_id,symbol,decision_timestamp,features_json,
                    labels_json,execution_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    candidate_id, None, decision.symbol,
                    decision.timestamp.astimezone(UTC).isoformat(),
                    encoded_features, _json({}), _json(execution), now, now,
                ),
            )
        return self.get(candidate_id)

    def record_scanner_decision(
        self,
        decision: ScannerDecision,
        *,
        execution_environment: str = "PAPER",
        strategy_version: str = DEFAULT_STRATEGY_VERSION,
        model_version: str = DEFAULT_MODEL_VERSION,
    ) -> CandidateRecord:
        """Label older opportunities, then persist this immutable observation."""

        if decision.timestamp is None or decision.price is None:
            raise ValueError("complete scanner decision is required")
        self.observe_price(decision.symbol, decision.timestamp, decision.price)
        from app.live_scanner.session import scanner_session

        return self.record_candidate(
            decision,
            market_session=scanner_session(decision.timestamp).value,
            scanner_rank=decision.scanner_rank,
            execution_environment=execution_environment,
            strategy_version=strategy_version,
            model_version=model_version,
        )

    def observe_price(
        self, symbol: str, timestamp: datetime, price: Decimal
    ) -> int:
        normalized = symbol.strip().upper()
        if not normalized or timestamp.tzinfo is None:
            raise ValueError("symbol and timezone-aware timestamp are required")
        observed = Decimal(price)
        if not observed.is_finite() or observed <= 0:
            raise ValueError("observed price must be positive and finite")
        changed = 0
        observed_at = timestamp.astimezone(UTC)
        counterfactual_cutoff = (
            observed_at
            - timedelta(
                seconds=HORIZONS_SECONDS["30m"] + 300
            )
        ).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM experiment_candidates
                   WHERE symbol=? AND decision_timestamp<=?
                     AND (
                       decision_timestamp>=?
                       OR json_extract(execution_json, '$.state') IN (?,?)
                     )""",
                (
                    normalized, observed_at.isoformat(), counterfactual_cutoff,
                    ExecutionState.PARTIALLY_FILLED.value,
                    ExecutionState.FILLED.value,
                ),
            ).fetchall()
            for row in rows:
                decision_at = datetime.fromisoformat(row["decision_timestamp"])
                elapsed = (timestamp.astimezone(UTC) - decision_at).total_seconds()
                if elapsed < 0:
                    continue
                features = json.loads(row["features_json"])
                labels = json.loads(row["labels_json"])
                execution = json.loads(row["execution_json"])
                counterfactual_active = elapsed <= HORIZONS_SECONDS["30m"] + 300
                if counterfactual_active:
                    reference = Decimal(features["counterfactual_reference_price"])
                    move = (observed - reference) / reference
                    if elapsed <= HORIZONS_SECONDS["30m"]:
                        labels["mfe"] = str(max(
                            Decimal(labels.get("mfe", "0")), move
                        ))
                        labels["mae"] = str(min(
                            Decimal(labels.get("mae", "0")), move
                        ))
                    labels["last_observed_at"] = timestamp.astimezone(UTC).isoformat()
                    for name, seconds in HORIZONS_SECONDS.items():
                        price_key = f"price_after_{name}"
                        if elapsed >= seconds and price_key not in labels:
                            labels[price_key] = str(observed)
                            labels[f"return_after_{name}"] = str(move)
                    labels["outcome_status"] = (
                        "COMPLETE" if "price_after_30m" in labels else "PENDING"
                    )
                entry_at_text = execution.get("entry_timestamp")
                average_fill_text = execution.get("average_fill_price")
                actual_active = False
                if entry_at_text is not None and average_fill_text is not None:
                    entry_at = datetime.fromisoformat(entry_at_text)
                    exit_at_text = execution.get("exit_timestamp")
                    exit_at = (
                        None if exit_at_text is None
                        else datetime.fromisoformat(exit_at_text)
                    )
                    actual_active = (
                        timestamp.astimezone(UTC) >= entry_at
                        and (exit_at is None or timestamp.astimezone(UTC) <= exit_at)
                    )
                    if actual_active:
                        actual_reference = Decimal(average_fill_text)
                        side = execution.get("side", "BUY")
                        actual_move = (
                            (observed - actual_reference) / actual_reference
                            if side == "BUY"
                            else (actual_reference - observed) / actual_reference
                        )
                        execution["actual_mfe"] = str(max(
                            Decimal(execution.get("actual_mfe", "0")), actual_move
                        ))
                        execution["actual_mae"] = str(min(
                            Decimal(execution.get("actual_mae", "0")), actual_move
                        ))
                if not counterfactual_active and not actual_active:
                    continue
                connection.execute(
                    "UPDATE experiment_candidates SET labels_json=?,execution_json=?,updated_at=? WHERE candidate_id=?",
                    (
                        _json(labels), _json(execution), datetime.now(UTC).isoformat(),
                        row["candidate_id"],
                    ),
                )
                changed += 1
        return changed

    def record_submission(
        self,
        candidate_id: str,
        *,
        requested_quantity: Decimal,
        order_type: str,
        submitted_price: Decimal | None,
        client_order_id: str | None = None,
        order_id: str | None = None,
        accepted: bool = True,
        rejection_reason: str | None = None,
        live_trading_enabled: bool = False,
        side: str = "BUY",
        stop_price: Decimal | None = None,
        target_price: Decimal | None = None,
        risk_per_share: Decimal | None = None,
        planned_risk_dollars: Decimal | None = None,
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        _require_paper_execution(record, live_trading_enabled)
        if not bool(record.features["normal_qualifies"]):
            raise ValueError("normal strategy did not qualify; execution denied")
        quantity = Decimal(requested_quantity)
        if quantity <= 0:
            raise ValueError("requested quantity must be positive")
        deterministic_client_id = client_order_id or f"atlas-{candidate_id[:24]}"
        deterministic_trade_id = f"trade-{candidate_id}"
        execution = dict(record.execution)
        if execution.get("state") != ExecutionState.NOT_EXECUTED.value:
            if execution.get("client_order_id") == deterministic_client_id:
                return record
            raise ValueError("candidate already has an execution attempt")
        execution.update({
            "state": (
                ExecutionState.ACCEPTED.value if accepted
                else ExecutionState.REJECTED.value
            ),
            "paper_trade_executed": False,
            "requested_quantity": str(quantity),
            "order_type": order_type.strip().upper(),
            "side": side.strip().upper(),
            "submitted_price": None if submitted_price is None else str(submitted_price),
            "client_order_id": deterministic_client_id,
            "order_id": order_id,
            "rejection_reason": rejection_reason,
            "submitted_at": datetime.now(UTC).isoformat(),
            "state_history": [
                ExecutionState.SUBMITTED.value,
                (
                    ExecutionState.ACCEPTED.value if accepted
                    else ExecutionState.REJECTED.value
                ),
            ],
            "stop_price": _decimal_text(stop_price),
            "target_price": _decimal_text(target_price),
            "risk_per_share": _decimal_text(risk_per_share),
            "planned_risk_dollars": _decimal_text(planned_risk_dollars),
        })
        return self._update_execution(candidate_id, deterministic_trade_id, execution)

    def record_fill(
        self,
        candidate_id: str,
        *,
        fill_id: str,
        quantity: Decimal,
        price: Decimal,
        timestamp: datetime,
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        _require_paper_execution(record, False)
        execution = dict(record.execution)
        if execution.get("state") not in {
            ExecutionState.ACCEPTED.value,
            ExecutionState.PARTIALLY_FILLED.value,
        }:
            raise ValueError("fill requires an accepted paper order")
        if fill_id in execution.get("fill_ids", []):
            return record
        fill_quantity = Decimal(quantity)
        fill_price = Decimal(price)
        if fill_quantity <= 0 or fill_price <= 0 or timestamp.tzinfo is None:
            raise ValueError("fill quantity, price, and aware timestamp are required")
        previous_quantity = Decimal(execution.get("fill_quantity", "0"))
        previous_average = Decimal(execution.get("average_fill_price") or "0")
        total_quantity = previous_quantity + fill_quantity
        requested = Decimal(execution["requested_quantity"])
        if total_quantity > requested:
            raise ValueError("fill quantity exceeds requested quantity")
        average = (
            previous_average * previous_quantity + fill_price * fill_quantity
        ) / total_quantity
        fill_ids = list(execution.get("fill_ids", [])) + [fill_id]
        reference = Decimal(record.features["last_price"])
        execution.update({
            "state": (
                ExecutionState.FILLED.value
                if total_quantity == requested
                else ExecutionState.PARTIALLY_FILLED.value
            ),
            "paper_trade_executed": True,
            "fill_ids": fill_ids,
            "fill_quantity": str(total_quantity),
            "average_fill_price": str(average),
            "slippage": str(average - reference),
            "entry_timestamp": execution.get("entry_timestamp")
            or timestamp.astimezone(UTC).isoformat(),
            "state_history": [
                *execution.get("state_history", []),
                (
                    ExecutionState.FILLED.value
                    if total_quantity == requested
                    else ExecutionState.PARTIALLY_FILLED.value
                ),
            ],
        })
        return self._update_execution(candidate_id, record.trade_id, execution)

    def record_cancellation(
        self, candidate_id: str, *, reason: str, timestamp: datetime
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        execution = dict(record.execution)
        if execution.get("state") not in {
            ExecutionState.ACCEPTED.value,
            ExecutionState.PARTIALLY_FILLED.value,
        }:
            raise ValueError("only an open paper order can be cancelled")
        if timestamp.tzinfo is None:
            raise ValueError("cancellation timestamp must be timezone-aware")
        execution.update({
            "state": ExecutionState.CANCELLED.value,
            "cancelled_at": timestamp.astimezone(UTC).isoformat(),
            "cancellation_reason": reason.strip(),
            "state_history": [
                *execution.get("state_history", []), ExecutionState.CANCELLED.value
            ],
        })
        return self._update_execution(candidate_id, record.trade_id, execution)

    def record_replacement(
        self,
        candidate_id: str,
        *,
        replacement_order_id: str,
        replacement_price: Decimal | None,
        timestamp: datetime,
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        execution = dict(record.execution)
        if execution.get("state") not in {
            ExecutionState.ACCEPTED.value,
            ExecutionState.PARTIALLY_FILLED.value,
        }:
            raise ValueError("only an open paper order can be replaced")
        if timestamp.tzinfo is None or not replacement_order_id.strip():
            raise ValueError("replacement identity and aware timestamp are required")
        replacements = list(execution.get("replacements", []))
        replacements.append({
            "order_id": replacement_order_id.strip(),
            "price": None if replacement_price is None else str(replacement_price),
            "timestamp": timestamp.astimezone(UTC).isoformat(),
        })
        execution["replacements"] = replacements
        return self._update_execution(candidate_id, record.trade_id, execution)

    def record_exit(
        self,
        candidate_id: str,
        *,
        exit_price: Decimal,
        exit_reason: str,
        timestamp: datetime,
    ) -> CandidateRecord:
        record = self.get(candidate_id)
        execution = dict(record.execution)
        quantity = Decimal(execution.get("fill_quantity", "0"))
        entry = Decimal(execution.get("average_fill_price") or "0")
        price = Decimal(exit_price)
        if quantity <= 0 or entry <= 0:
            raise ValueError("actual filled quantity is required before exit")
        if price <= 0 or timestamp.tzinfo is None:
            raise ValueError("positive exit price and aware timestamp are required")
        entry_at = datetime.fromisoformat(execution["entry_timestamp"])
        exit_move = (
            (price - entry) / entry
            if execution.get("side", "BUY") == "BUY"
            else (entry - price) / entry
        )
        realized_pnl = (
            (price - entry) * quantity
            if execution.get("side", "BUY") == "BUY"
            else (entry - price) * quantity
        )
        execution.update({
            "state": ExecutionState.CLOSED.value,
            "exit_timestamp": timestamp.astimezone(UTC).isoformat(),
            "exit_price": str(price),
            "exit_reason": exit_reason.strip(),
            "realized_pnl": str(realized_pnl),
            "return_percent": str(exit_move * Decimal("100")),
            "actual_mfe": str(max(
                Decimal(execution.get("actual_mfe", "0")), exit_move
            )),
            "actual_mae": str(min(
                Decimal(execution.get("actual_mae", "0")), exit_move
            )),
            "holding_seconds": int((timestamp.astimezone(UTC) - entry_at).total_seconds()),
            "state_history": [
                *execution.get("state_history", []), ExecutionState.CLOSED.value
            ],
        })
        return self._update_execution(candidate_id, record.trade_id, execution)

    def get(self, candidate_id: str) -> CandidateRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiment_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return _record(row)

    def records(self) -> tuple[CandidateRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM experiment_candidates ORDER BY decision_timestamp,candidate_id"
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def pending(self, symbol: str | None = None) -> tuple[CandidateRecord, ...]:
        records = self.records()
        return tuple(
            item for item in records
            if (symbol is None or item.symbol == symbol.strip().upper())
            and item.labels.get("outcome_status") != "COMPLETE"
        )

    def record_coordination_result(self, coordination: object) -> CandidateRecord | None:
        """Project an existing coordinator result; never performs execution."""

        decision = getattr(coordination, "strategy_decision", None)
        intent = getattr(coordination, "order_intent", None)
        if decision is None or intent is None:
            return None
        action = str(getattr(getattr(decision, "action", None), "value", ""))
        symbol = str(getattr(decision, "symbol", "")).strip().upper()
        if action.startswith("EXIT_"):
            opened = tuple(
                item for item in self.records()
                if item.symbol == symbol
                and item.execution.get("paper_trade_executed") is True
                and item.execution.get("state") in {
                    ExecutionState.FILLED.value,
                    ExecutionState.PARTIALLY_FILLED.value,
                }
            )
            simulation = getattr(coordination, "execution_result", None)
            execution_result = getattr(simulation, "execution", None)
            fill = getattr(execution_result, "fill", None)
            if not opened or fill is None:
                return None
            return self.record_exit(
                opened[-1].candidate_id, exit_price=fill.fill_price,
                exit_reason="EXISTING_STRATEGY_EXIT", timestamp=fill.timestamp,
            )

        if not action.startswith("ENTER_"):
            return None
        eligible = tuple(
            item for item in self.records()
            if item.symbol == symbol
            and item.features.get("normal_qualifies") is True
            and item.execution.get("state") == ExecutionState.NOT_EXECUTED.value
            and datetime.fromisoformat(str(item.features["decision_timestamp"]))
            <= decision.timestamp.astimezone(UTC)
        )
        if not eligible:
            return None
        candidate = eligible[-1]
        status = str(getattr(getattr(coordination, "status", None), "value", ""))
        simulation = getattr(coordination, "execution_result", None)
        execution_result = getattr(simulation, "execution", None)
        fill = getattr(execution_result, "fill", None)
        accepted = status == "EXECUTED" and fill is not None
        candidate = self.record_submission(
            candidate.candidate_id,
            requested_quantity=intent.quantity,
            order_type=intent.order_type.value,
            submitted_price=intent.limit_price,
            client_order_id=intent.request_id,
            order_id=intent.request_id,
            accepted=accepted,
            rejection_reason=None if accepted else _coordination_reason(coordination),
            live_trading_enabled=False,
            side=intent.side.value,
            stop_price=intent.stop_price,
        )
        execution = dict(candidate.execution)
        execution["execution_strategy_version"] = str(
            getattr(decision, "strategy_version", "unknown")
        )
        candidate = self._update_execution(
            candidate.candidate_id, candidate.trade_id, execution
        )
        if intent.stop_price is not None:
            execution = dict(candidate.execution)
            reference = Decimal(candidate.features["last_price"])
            risk_per_share = abs(reference - intent.stop_price)
            execution.update({
                "stop_price": str(intent.stop_price),
                "risk_per_share": str(risk_per_share),
                "planned_risk_dollars": str(risk_per_share * intent.quantity),
            })
            candidate = self._update_execution(
                candidate.candidate_id, candidate.trade_id, execution
            )
        if fill is None:
            return candidate
        return self.record_fill(
            candidate.candidate_id,
            fill_id=f"{fill.request_id}:{fill.timestamp.astimezone(UTC).isoformat()}",
            quantity=fill.quantity,
            price=fill.fill_price,
            timestamp=fill.timestamp,
        )

    def _update_execution(
        self, candidate_id: str, trade_id: str | None, execution: Mapping[str, Any]
    ) -> CandidateRecord:
        _assert_no_secrets(execution)
        with self._connect() as connection:
            connection.execute(
                "UPDATE experiment_candidates SET trade_id=?,execution_json=?,updated_at=? WHERE candidate_id=?",
                (trade_id, _json(execution), datetime.now(UTC).isoformat(), candidate_id),
            )
        return self.get(candidate_id)


def _decision_features(
    decision: ScannerDecision,
    *, market_session: str, scanner_rank: int | None,
    strategy_version: str, model_version: str,
    application_commit: str | None, execution_environment: str,
) -> dict[str, Any]:
    source_url = _safe_url(decision.catalyst_source_url)
    return {
        "feature_schema": SCHEMA_VERSION,
        "strategy_version": strategy_version,
        "model_version": model_version,
        "application_commit": application_commit,
        "execution_environment": execution_environment,
        "symbol": decision.symbol,
        "market_session": market_session.strip().upper(),
        "decision_timestamp": decision.timestamp.astimezone(UTC).isoformat(),
        "scanner_rank": decision.scanner_rank if scanner_rank is None else scanner_rank,
        "scanner_score": decision.score,
        "last_price": str(decision.price),
        "previous_close": _decimal_text(decision.previous_close),
        "percentage_change": str(decision.metrics.percentage_change),
        "relative_volume": str(decision.metrics.relative_volume),
        "current_volume": _decimal_text(decision.current_volume),
        "average_30_day_volume": _decimal_text(decision.average_30_day_volume),
        "float_shares": _decimal_text(decision.float_shares),
        "dollar_volume": str(decision.metrics.dollar_volume),
        "bid": _decimal_text(decision.bid),
        "ask": _decimal_text(decision.ask),
        "spread_percent": _decimal_text(decision.metrics.spread_percent),
        "tradable": decision.tradable,
        "halted": decision.halted,
        "catalyst_status": decision.catalyst_status.value,
        "catalyst_type": decision.catalyst.value,
        "selected_source": decision.catalyst_source,
        "headline": decision.catalyst_headline,
        "published_at": (
            decision.catalyst_published_at.astimezone(UTC).isoformat()
            if decision.catalyst_published_at is not None else None
        ),
        "source_url": source_url,
        "corroborating_sources": list(decision.corroborating_sources),
        "evidence_count": decision.catalyst_evidence_count,
        "event_count": decision.catalyst_event_count,
        "normal_qualifies": decision.qualified,
        "technical_qualifies_without_catalyst": decision.technical_qualifies_without_catalyst,
        "passed_rules": list(decision.passed_rules),
        "failed_rules": list(decision.failed_rules),
        "technical_passed_rules": list(decision.technical_passed_rules),
        "technical_failed_rules": list(decision.technical_failed_rules),
        "cohort_flags": list(decision.cohort_flags),
        "counterfactual_reference_price": str(decision.price),
        "counterfactual_reference_kind": "OBSERVED_MARKET_PRICE_AT_DECISION",
    }


def _candidate_id(decision: ScannerDecision, strategy_version: str) -> str:
    payload = "\x1f".join((
        decision.symbol,
        decision.timestamp.astimezone(UTC).isoformat() if decision.timestamp else "",
        str(decision.price), strategy_version,
    ))
    return "candidate-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_environment(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in _SAFE_EXECUTION_ENVIRONMENTS:
        raise ValueError("paper experiment execution environment must be PAPER or TEST")
    return normalized


def _safe_application_commit() -> str | None:
    value = os.environ.get("ATLAS_APPLICATION_COMMIT", "").strip()
    return value if re.fullmatch(r"[0-9a-fA-F]{7,64}", value) else None


def _require_paper_execution(record: CandidateRecord, live_enabled: bool) -> None:
    if live_enabled:
        raise PermissionError("LIVE_TRADING_ENABLED must remain false")
    _safe_environment(str(record.features.get("execution_environment", "")))


def _safe_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _assert_no_secrets(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold()
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                raise ValueError(f"secret-bearing journal field rejected: {path}.{key}")
            _assert_no_secrets(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_secrets(item, f"{path}[{index}]")


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record(row: sqlite3.Row) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=row["candidate_id"], trade_id=row["trade_id"],
        features=json.loads(row["features_json"]),
        labels=json.loads(row["labels_json"]),
        execution=json.loads(row["execution_json"]),
    )


def read_records(path: str | Path) -> tuple[CandidateRecord, ...]:
    """Read a journal without creating, migrating, or writing it."""

    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"paper experiment journal not found: {resolved}")
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        version = connection.execute(
            "SELECT value FROM experiment_metadata WHERE key='schema_version'"
        ).fetchone()
        if version is None or version["value"] != SCHEMA_VERSION:
            raise ValueError("unsupported paper experiment journal schema")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError("paper experiment journal integrity check failed")
        rows = connection.execute(
            "SELECT * FROM experiment_candidates ORDER BY decision_timestamp,candidate_id"
        ).fetchall()
        return tuple(_record(row) for row in rows)
    finally:
        connection.close()


def _coordination_reason(coordination: object) -> str:
    trace = getattr(coordination, "trace", ())
    if trace:
        return str(getattr(trace[-1], "message", "paper execution rejected"))
    return "paper execution rejected"


__all__ = [
    "DEFAULT_MODEL_VERSION", "DEFAULT_STRATEGY_VERSION",
    "PaperTradeExperimentJournal", "SCHEMA_VERSION", "read_records",
]

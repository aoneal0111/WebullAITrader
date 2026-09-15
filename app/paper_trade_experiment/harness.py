"""Fail-closed, PAPER-only controlled experiment harness.

This module is deliberately a sidecar.  It does not evaluate strategies,
place orders, or alter production policies.  Callers must explicitly opt in
to :class:`ExperimentRouter` and explicitly enable an experiment definition.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from uuid import uuid4
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping


FRAMEWORK_VERSION = "ATLAS_PAPER_EXPERIMENTS_V1"
CONTROL_ARM = "CONTROL"
TREATMENT_ARM = "TREATMENT"
PAPER_MODES = frozenset({"PAPER", "TEST"})


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    experiment_id: str
    version: str
    strategy: str
    control_arm: Mapping[str, Any]
    treatment_arm: Mapping[str, Any]
    start_timestamp: str
    enabled: bool = False
    paper_only: bool = True
    assignment_policy: str = "SHA256_EXPERIMENT_STRATEGY_SYMBOL_DATE_IDENTITY"
    control_allocation_percent: int = 50
    treatment_allocation_percent: int = 50
    minimum_sample_target: int = 0
    rejection_criteria: tuple[str, ...] = ()
    promotion_review_criteria: tuple[str, ...] = ()
    research_provenance: str = ""
    stratification: tuple[str, ...] = ()
    experimental_dimension: str = "policy"

    def __post_init__(self) -> None:
        if not self.experiment_id.strip() or not self.version.strip():
            raise ValueError("experiment identity is required")
        if not self.strategy.strip():
            raise ValueError("experiment strategy is required")
        if not self.paper_only:
            raise ValueError("paper_only must be true")
        if self.control_allocation_percent + self.treatment_allocation_percent != 100:
            raise ValueError("arm allocation must total 100")
        if min(self.control_allocation_percent, self.treatment_allocation_percent) < 0:
            raise ValueError("arm allocation cannot be negative")
        if self.minimum_sample_target < 0:
            raise ValueError("minimum sample target cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "version": self.version,
            "strategy": self.strategy,
            "control_arm": dict(self.control_arm),
            "treatment_arm": dict(self.treatment_arm),
            "start_timestamp": self.start_timestamp,
            "enabled": self.enabled,
            "paper_only": self.paper_only,
            "assignment_policy": self.assignment_policy,
            "control_allocation_percent": self.control_allocation_percent,
            "treatment_allocation_percent": self.treatment_allocation_percent,
            "minimum_sample_target": self.minimum_sample_target,
            "rejection_criteria": list(self.rejection_criteria),
            "promotion_review_criteria": list(self.promotion_review_criteria),
            "research_provenance": self.research_provenance,
            "stratification": list(self.stratification),
            "experimental_dimension": self.experimental_dimension,
        }


@dataclass(frozen=True, slots=True)
class ExperimentOpportunity:
    assignment_identity: str
    strategy: str
    symbol: str
    trading_date: str
    decision_timestamp: str
    context: Mapping[str, Any] = field(default_factory=dict)
    entry_policy_identity: str = "CURRENT_ATLAS_ENTRY"
    exit_policy_identity: str = "CURRENT_ATLAS_EXIT"
    risk_policy_identity: str = "CURRENT_ATLAS_RISK"

    def __post_init__(self) -> None:
        if not self.assignment_identity.strip() or not self.strategy.strip():
            raise ValueError("assignment identity and strategy are required")
        if not self.symbol.strip() or not self.trading_date.strip():
            raise ValueError("symbol and trading date are required")


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    assignment_id: str
    experiment_id: str | None
    arm: str
    persisted: bool
    fallback: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RouteResult:
    assignment: AssignmentResult
    selected_policy: Mapping[str, Any]
    treatment_executed: bool
    sidecar_error: str | None = None


def _assignment_id(definition: ExperimentDefinition, opportunity: ExperimentOpportunity) -> str:
    material = "|".join((definition.experiment_id, definition.version,
                          opportunity.strategy, opportunity.symbol,
                          opportunity.trading_date, opportunity.assignment_identity))
    return "assignment-" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _bucket(definition: ExperimentDefinition, opportunity: ExperimentOpportunity) -> int:
    material = "|".join((definition.experiment_id, opportunity.strategy,
                          opportunity.symbol, opportunity.trading_date,
                          opportunity.assignment_identity))
    return int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big") % 100


def one_dimension_delta(definition: ExperimentDefinition) -> tuple[str, ...]:
    """Return changed arm keys so callers can enforce isolated treatments."""

    keys = set(definition.control_arm) | set(definition.treatment_arm)
    return tuple(sorted(key for key in keys
                        if definition.control_arm.get(key) != definition.treatment_arm.get(key)))


class PaperExperimentJournal:
    """Append-only experiment definitions, assignments, and outcomes.

    SQLite connections are deliberately short-lived and never cross a thread
    boundary.  This journal is called from both the desktop and market-data
    callback threads, so each operation owns its connection and transaction.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._closed = False
        self._connection = self._new_connection()
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiment_definitions (
                experiment_id TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS experiment_assignments (
                assignment_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                experiment_version TEXT NOT NULL,
                arm TEXT NOT NULL CHECK(arm IN ('CONTROL','TREATMENT')),
                assignment_identity TEXT NOT NULL,
                strategy TEXT NOT NULL,
                symbol TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                decision_timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                entry_policy_identity TEXT NOT NULL,
                exit_policy_identity TEXT NOT NULL,
                risk_policy_identity TEXT NOT NULL,
                context_json TEXT NOT NULL,
                exclusion_reason TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(experiment_id, assignment_identity)
            );
            CREATE INDEX IF NOT EXISTS experiment_assignments_experiment_arm
                ON experiment_assignments(experiment_id, arm);
            CREATE TABLE IF NOT EXISTS experiment_outcomes (
                outcome_id TEXT PRIMARY KEY,
                assignment_id TEXT NOT NULL REFERENCES experiment_assignments(assignment_id),
                trade_id TEXT,
                order_id TEXT,
                outcome_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(assignment_id, outcome_id)
            );
            CREATE TABLE IF NOT EXISTS experiment_decisions (
                assignment_id TEXT PRIMARY KEY REFERENCES experiment_assignments(assignment_id),
                control_decision TEXT NOT NULL,
                treatment_decision TEXT NOT NULL,
                selected_mode TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS experiment_shadows (
                shadow_id TEXT PRIMARY KEY,
                assignment_id TEXT NOT NULL REFERENCES experiment_assignments(assignment_id),
                shadow_type TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                price TEXT,
                stage TEXT,
                shadow_json TEXT NOT NULL,
                UNIQUE(assignment_id, shadow_type)
            );
            CREATE TABLE IF NOT EXISTS experiment_assignment_links (
                assignment_id TEXT PRIMARY KEY REFERENCES experiment_assignments(assignment_id),
                lifecycle_id TEXT NOT NULL UNIQUE,
                linked_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS experiment_execution_events (
                event_id TEXT PRIMARY KEY,
                assignment_id TEXT NOT NULL REFERENCES experiment_assignments(assignment_id),
                event_type TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                order_id TEXT,
                fill_id TEXT,
                price TEXT,
                quantity TEXT,
                event_json TEXT NOT NULL,
                UNIQUE(assignment_id, event_type, order_id, fill_id)
            );
            CREATE TABLE IF NOT EXISTS experiment_runtime_markers (
                marker_id TEXT PRIMARY KEY,
                marker_type TEXT NOT NULL,
                startup_timestamp TEXT NOT NULL,
                trading_environment TEXT NOT NULL,
                live_trading_enabled INTEGER NOT NULL CHECK(live_trading_enabled IN (0,1)),
                warrior_forward_paper_enabled INTEGER NOT NULL CHECK(warrior_forward_paper_enabled IN (0,1)),
                historical_entry_experiment_enabled INTEGER NOT NULL CHECK(historical_entry_experiment_enabled IN (0,1)),
                historical_entry_experiment_mode TEXT NOT NULL,
                historical_entry_experiment_path TEXT NOT NULL,
                paper_symbol_authorization_mode TEXT NOT NULL,
                experiment_id TEXT NOT NULL,
                experiment_version TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _connect(self) -> sqlite3.Connection:
        """Open one connection owned by the calling thread."""
        if self._closed:
            raise RuntimeError("experiment journal is closed")
        return self._new_connection()

    @contextmanager
    def _connection_scope(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def record_runtime_marker(
        self, *, trading_environment: str, live_trading_enabled: bool,
        warrior_forward_paper_enabled: bool,
        historical_entry_experiment_enabled: bool,
        historical_entry_experiment_mode: str,
        historical_entry_experiment_path: str,
        paper_symbol_authorization_mode: str,
        experiment_id: str,
        experiment_version: str,
    ) -> str:
        """Durably record the effective, non-secret startup configuration."""
        marker_id = "startup-" + uuid4().hex
        with self._connection_scope() as connection:
            connection.execute(
            """INSERT INTO experiment_runtime_markers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                marker_id, "POLICY_INITIALIZED", _now(),
                str(trading_environment), int(live_trading_enabled),
                int(warrior_forward_paper_enabled),
                int(historical_entry_experiment_enabled),
                str(historical_entry_experiment_mode),
                str(historical_entry_experiment_path),
                str(paper_symbol_authorization_mode), str(experiment_id),
                str(experiment_version),
            ),
        )
            connection.commit()
        return marker_id

    def record_decision(self, assignment_id: str, *, control_decision: str,
                        treatment_decision: str, selected_mode: str,
                        decision: Mapping[str, Any]) -> bool:
        """Persist one decision/shadow observation idempotently."""
        try:
            with self._connection_scope() as connection:
                connection.execute(
                """INSERT INTO experiment_decisions VALUES(?,?,?,?,?,?)
                   ON CONFLICT(assignment_id) DO NOTHING""",
                (assignment_id, control_decision, treatment_decision,
                 selected_mode, _json(decision), _now()),
            )
                changed = connection.execute("SELECT changes()").fetchone()[0]
                connection.commit()
                return bool(changed)
        except Exception:
            if 'connection' in locals():
                connection.rollback()
            return False

    def record_shadow(self, assignment_id: str, *, shadow_type: str,
                      observed_at: str, price: Any = None, stage: str = "",
                      shadow: Mapping[str, Any] | None = None) -> bool:
        """Persist one counterfactual control/treatment observation."""
        shadow_id = "shadow-" + hashlib.sha256(
            f"{assignment_id}|{shadow_type}".encode("utf-8")
        ).hexdigest()
        try:
            with self._connection_scope() as connection:
                connection.execute(
                """INSERT INTO experiment_shadows VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(assignment_id, shadow_type) DO NOTHING""",
                (shadow_id, assignment_id, shadow_type, observed_at,
                 None if price is None else str(price), stage,
                 _json(shadow or {})),
            )
                changed = connection.execute("SELECT changes()").fetchone()[0]
                connection.commit()
                return bool(changed)
        except Exception:
            if 'connection' in locals():
                connection.rollback()
            return False

    def link_lifecycle(self, assignment_id: str, lifecycle_id: str) -> bool:
        try:
            with self._connection_scope() as connection:
                connection.execute(
                "INSERT INTO experiment_assignment_links VALUES(?,?,?) "
                "ON CONFLICT(assignment_id) DO UPDATE SET lifecycle_id=excluded.lifecycle_id",
                (assignment_id, lifecycle_id, _now()),
            )
                connection.commit()
                return True
        except Exception:
            if 'connection' in locals():
                connection.rollback()
            return False

    def assignment_for_lifecycle(self, lifecycle_id: str):
        with self._connection_scope() as connection:
            return connection.execute(
                "SELECT assignment_id FROM experiment_assignment_links WHERE lifecycle_id=?",
                (lifecycle_id,),
            ).fetchone()

    def record_execution_event(self, assignment_id: str, *, event_id: str,
                               event_type: str, observed_at: str,
                               order_id: str | None = None, fill_id: str | None = None,
                               price: Any = None, quantity: Any = None,
                               event: Mapping[str, Any] | None = None) -> bool:
        try:
            with self._connection_scope() as connection:
                connection.execute(
                """INSERT INTO experiment_execution_events VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(event_id) DO NOTHING""",
                (event_id, assignment_id, event_type, observed_at, order_id, fill_id,
                 None if price is None else str(price),
                 None if quantity is None else str(quantity), _json(event or {})),
            )
                changed = connection.execute("SELECT changes()").fetchone()[0]
                connection.commit()
                return bool(changed)
        except Exception:
            if 'connection' in locals():
                connection.rollback()
            return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._connection.close()

    def register(self, definition: ExperimentDefinition) -> None:
        with self._connection_scope() as connection:
            connection.execute(
            """INSERT INTO experiment_definitions VALUES(?,?,?,?,?)
               ON CONFLICT(experiment_id) DO UPDATE SET version=excluded.version,
               definition_json=excluded.definition_json, enabled=excluded.enabled,
               updated_at=excluded.updated_at""",
            (definition.experiment_id, definition.version, _json(definition.to_dict()),
             int(definition.enabled), _now()),
        )
            connection.commit()

    def assignment(
        self, definition: ExperimentDefinition, opportunity: ExperimentOpportunity,
        *, mode: str, arm: str, exclusion_reason: str | None = None,
    ) -> AssignmentResult:
        assignment_id = _assignment_id(definition, opportunity)
        try:
            with self._connection_scope() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                """SELECT assignment_id, arm FROM experiment_assignments
                   WHERE experiment_id=? AND assignment_identity=?""",
                (definition.experiment_id, opportunity.assignment_identity),
            ).fetchone()
                if existing is not None:
                    connection.commit()
                    return AssignmentResult(existing["assignment_id"], definition.experiment_id,
                                            existing["arm"], True, False, "ALREADY_ASSIGNED")
                connection.execute(
                """INSERT INTO experiment_assignments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(assignment_id) DO NOTHING""",
                (assignment_id, definition.experiment_id, definition.version, arm,
                 opportunity.assignment_identity, opportunity.strategy, opportunity.symbol,
                 opportunity.trading_date, opportunity.decision_timestamp, mode,
                 opportunity.entry_policy_identity, opportunity.exit_policy_identity,
                 opportunity.risk_policy_identity, _json(opportunity.context),
                 exclusion_reason, _now()),
            )
                connection.commit()
                return AssignmentResult(assignment_id, definition.experiment_id, arm, True, False, "ASSIGNED")
        except Exception as exc:  # sidecar failure must not escape the runtime
            if 'connection' in locals():
                connection.rollback()
            return AssignmentResult(assignment_id, None, CONTROL_ARM, False, True, type(exc).__name__)

    def record_outcome(
        self, assignment_id: str, outcome: Mapping[str, Any], *, outcome_id: str,
        trade_id: str | None = None, order_id: str | None = None,
    ) -> bool:
        try:
            with self._connection_scope() as connection:
                connection.execute(
                "INSERT INTO experiment_outcomes VALUES(?,?,?,?,?,?) ON CONFLICT(outcome_id) DO NOTHING",
                (outcome_id, assignment_id, trade_id, order_id, _json(outcome), _now()),
            )
                changed = connection.execute("SELECT changes()").fetchone()[0]
                connection.commit()
                return bool(changed)
        except Exception:
            if 'connection' in locals():
                connection.rollback()
            return False

    def status(self) -> dict[str, Any]:
        with self._connection_scope() as connection:
            definitions = []
            for row in connection.execute(
                "SELECT * FROM experiment_definitions ORDER BY experiment_id"
            ):
                definition = json.loads(row["definition_json"])
                counts = connection.execute(
                    """SELECT arm, COUNT(*) AS assigned,
                       SUM(EXISTS(SELECT 1 FROM experiment_outcomes o
                                  WHERE o.assignment_id=a.assignment_id)) AS completed
                       FROM experiment_assignments a
                       WHERE experiment_id=? GROUP BY arm""",
                    (row["experiment_id"],),
                ).fetchall()
                by_arm = {
                    x["arm"]: {"assigned": x["assigned"], "completed": x["completed"] or 0}
                    for x in counts
                }
                for arm in (CONTROL_ARM, TREATMENT_ARM):
                    by_arm.setdefault(arm, {"assigned": 0, "completed": 0})
                metrics_by_arm: dict[str, dict[str, Any]] = {}
                for arm in (CONTROL_ARM, TREATMENT_ARM):
                    outcome_rows = connection.execute(
                        """SELECT o.outcome_json FROM experiment_outcomes o
                           JOIN experiment_assignments a ON a.assignment_id=o.assignment_id
                           WHERE a.experiment_id=? AND a.arm=?""",
                        (row["experiment_id"], arm),
                    ).fetchall()
                    numeric: dict[str, list[float]] = {}
                    for outcome_row in outcome_rows:
                        try:
                            outcome = json.loads(outcome_row["outcome_json"])
                        except (TypeError, ValueError):
                            continue
                        for key in ("PnL", "pnl", "R", "r", "MFE", "mfe", "MAE", "mae"):
                            value = outcome.get(key)
                            if isinstance(value, (int, float)) and not isinstance(value, bool):
                                numeric.setdefault(key.lower(), []).append(float(value))
                    metrics: dict[str, Any] = {"outcomes": len(outcome_rows)}
                    for key, values in numeric.items():
                        metrics[key + "_count"] = len(values)
                        metrics[key + "_mean"] = sum(values) / len(values)
                    metrics_by_arm[arm] = metrics
                context_breakdown: dict[str, dict[str, int]] = {}
                for assignment_row in connection.execute(
                    "SELECT arm, context_json FROM experiment_assignments WHERE experiment_id=?",
                    (row["experiment_id"],),
                ):
                    try:
                        context = json.loads(assignment_row["context_json"])
                    except (TypeError, ValueError):
                        context = {}
                    for dimension in ("market_regime", "session"):
                        value = context.get(dimension)
                        if value is not None:
                            dimension_counts = context_breakdown.setdefault(dimension, {})
                            dimension_counts[str(value)] = dimension_counts.get(str(value), 0) + 1
                exclusions = connection.execute(
                    """SELECT COUNT(*) FROM experiment_assignments
                       WHERE experiment_id=? AND exclusion_reason IS NOT NULL""",
                    (row["experiment_id"],),
                ).fetchone()[0]
                target = int(definition.get("minimum_sample_target", 0))
                assigned = sum(value["assigned"] for value in by_arm.values())
                definitions.append({
                    **definition,
                    "progress": by_arm,
                    "metrics": metrics_by_arm,
                    "context_breakdown": context_breakdown,
                    "exclusions": exclusions,
                    "assigned_observations": assigned,
                    "percent_toward_target": (
                        0 if target == 0 else min(100, assigned * 100 / target)
                    ),
                    "journal_health": "AVAILABLE",
                })
            return {"framework_version": FRAMEWORK_VERSION, "experiments": definitions}


class ExperimentRouter:
    """Explicit opt-in router; disabled or malformed state returns CONTROL."""

    def __init__(self, journal: PaperExperimentJournal, *, enabled: bool = False) -> None:
        self._journal = journal
        self._enabled = bool(enabled)

    def assign(
        self, definition: ExperimentDefinition, opportunity: ExperimentOpportunity,
        *, mode: str,
    ) -> AssignmentResult:
        normalized_mode = str(mode).strip().upper()
        try:
            assignment_id = _assignment_id(definition, opportunity)
            experiment_id = definition.experiment_id
        except Exception:
            return AssignmentResult("invalid-opportunity", None, CONTROL_ARM, False, True,
                                    "MALFORMED_CONFIGURATION")
        if normalized_mode == "LIVE":
            return AssignmentResult(assignment_id, experiment_id,
                                    CONTROL_ARM, False, True, "LIVE_BYPASS")
        if normalized_mode not in PAPER_MODES:
            return AssignmentResult(assignment_id, experiment_id,
                                    CONTROL_ARM, False, True, "UNSUPPORTED_MODE")
        if not self._enabled or not definition.enabled:
            try:
                return self._journal.assignment(definition, opportunity, mode=normalized_mode,
                                                arm=CONTROL_ARM, exclusion_reason="DISABLED")
            except Exception as exc:
                return AssignmentResult(assignment_id, experiment_id, CONTROL_ARM, False, True,
                                        f"SIDECAR_FAILURE:{type(exc).__name__}")
        arm = (TREATMENT_ARM if _bucket(definition, opportunity) >= definition.control_allocation_percent
               else CONTROL_ARM)
        try:
            return self._journal.assignment(definition, opportunity, mode=normalized_mode, arm=arm)
        except Exception as exc:
            return AssignmentResult(assignment_id, experiment_id, CONTROL_ARM, False, True,
                                    f"SIDECAR_FAILURE:{type(exc).__name__}")

    def route(
        self, definition: ExperimentDefinition, opportunity: ExperimentOpportunity,
        *, mode: str, treatment: Callable[[], Any] | None = None,
    ) -> RouteResult:
        assignment = self.assign(definition, opportunity, mode=mode)
        if assignment.arm != TREATMENT_ARM or assignment.fallback or treatment is None:
            return RouteResult(assignment, definition.control_arm, False,
                               None if assignment.persisted else assignment.reason)
        try:
            treatment()
            return RouteResult(assignment, definition.treatment_arm, True)
        except Exception as exc:  # treatment failure is also fail-closed
            return RouteResult(assignment, definition.control_arm, False, type(exc).__name__)


class ExperimentStatusService:
    """Read-only façade for CLI/diagnostic consumers."""

    def __init__(self, journal: PaperExperimentJournal) -> None:
        self._journal = journal

    def report(self) -> dict[str, Any]:
        return self._journal.status()


def wave_one_definitions() -> tuple[ExperimentDefinition, ...]:
    """Return disabled Wave-1 definitions; this function never activates them."""

    common = {"research_only": True, "paper_only": True}
    return (
        ExperimentDefinition(
            "wave1_hod_breakout_exit", "1", "HIGH_OF_DAY_BREAKOUT",
            {**common, "policy": "CURRENT_ATLAS_BEHAVIOR"},
            {**common, "policy": "5PCT_50_50_RUNNER"}, "2026-01-01T00:00:00+00:00",
            minimum_sample_target=300,
            rejection_criteria=("stop_first > 55%", "execution_adjusted_R <= 0"),
            promotion_review_criteria=("review only; no automatic promotion",),
            research_provenance="full_research_final.json; HIGH_OF_DAY_BREAKOUT",
            stratification=("strategy", "market_regime", "session"),
        ),
        ExperimentDefinition(
            "wave1_premarket_high_breakout_exit", "1", "PREMARKET_HIGH_BREAKOUT",
            {**common, "policy": "CURRENT_ATLAS_BEHAVIOR"},
            {**common, "policy": "8PCT_25_75_RUNNER"}, "2026-01-01T00:00:00+00:00",
            minimum_sample_target=250,
            rejection_criteria=("8pct conversion materially below 30-35%", "execution_adjusted_R collapse"),
            promotion_review_criteria=("review only; no automatic promotion",),
            research_provenance="full_research_final.json; PREMARKET_HIGH_BREAKOUT",
            stratification=("strategy", "market_regime", "session"),
        ),
        ExperimentDefinition(
            "wave1_first_pullback_target", "1", "FIRST_PULLBACK",
            {**common, "policy": "CURRENT_ATLAS_BEHAVIOR"},
            {**common, "policy": "2PCT_OR_1R_1P5R"}, "2026-01-01T00:00:00+00:00",
            minimum_sample_target=300,
            rejection_criteria=("MAE materially worse than control", "stop_first > 82%"),
            promotion_review_criteria=("review only; no automatic promotion",),
            research_provenance="full_research_final.json; FIRST_PULLBACK",
            stratification=("strategy", "market_regime", "session"),
        ),
    )


__all__ = [
    "CONTROL_ARM", "ExperimentDefinition", "ExperimentOpportunity",
    "ExperimentRouter", "ExperimentStatusService", "FRAMEWORK_VERSION",
    "PaperExperimentJournal", "RouteResult", "TREATMENT_ARM", "one_dimension_delta",
    "wave_one_definitions",
]

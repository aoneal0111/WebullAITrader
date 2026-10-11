"""Explicit worker-side AI entry handoff to the existing PAPER controller."""
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from threading import Lock

from app.asset_modules.controlled_paper_service import ControlledPaperTradingService
from app.asset_modules.engine_catalog import EngineId, lifecycle_owner
from app.asset_modules.grok_coordinator import Candidate, GrokSuite, IntentKind
from app.order_placement import OrderPlacementRequest


@dataclass(frozen=True)
class PreparedEntry:
    candidate: Candidate
    placement: OrderPlacementRequest

    @classmethod
    def from_placement(cls, placement, *, evidence):
        """Bind a native owned placement to advisory evidence without rewriting it."""
        if not isinstance(placement, OrderPlacementRequest) or not isinstance(evidence, dict):
            raise ValueError("Native placement and structured evidence required")
        r = placement.order
        engine = lifecycle_owner(r.strategy_lifecycle_id)
        expected_owner = {EngineId.WARRIOR: "WARRIOR_MOMENTUM", EngineId.SCALPER: "QUICK_SCALPER"}.get(engine)
        if expected_owner is None or r.metadata.get("strategy_owner") != expected_owner:
            raise ValueError("Original strategy ownership metadata required")
        stop = Decimal(str(r.metadata.get("structural_stop")))
        risk = Decimal(str(r.metadata.get("risk_dollars")))
        if (r.limit_price is None or stop is None or not stop.is_finite()
                or not 0 < stop < r.limit_price or not risk.is_finite() or risk <= 0
                or not r.metadata.get("opportunity_id")):
            raise ValueError("Structural stop, risk and opportunity required")
        # Canonical economics override extra evidence; AI can only select this
        # exact placement, never edit its quantity, prices or lifecycle.
        context = dict(evidence)
        context["placement"] = {"symbol": r.symbol, "quantity": str(r.quantity),
            "limit_price": str(r.limit_price), "structural_stop": str(stop),
            "risk_dollars": str(risk), "opportunity_id": r.metadata["opportunity_id"]}
        deadline = datetime.fromisoformat(str(r.metadata.get("chase_deadline")))
        key = sha256((placement.session_id + "|" + r.account_id + "|"
                      + r.strategy_lifecycle_id + "|" + r.client_order_id).encode()).hexdigest()
        c = Candidate(engine, engine.value + "|" + key, deadline,
            json.dumps(context, allow_nan=False, separators=(",", ":")), lifecycle_id=r.strategy_lifecycle_id)
        return cls(c, placement)

    def __post_init__(self):
        c, p = self.candidate, self.placement
        if not isinstance(c, Candidate) or not isinstance(p, OrderPlacementRequest):
            raise ValueError("Trusted immutable candidate and placement required")
        r = p.order
        owner = {EngineId.WARRIOR: "WARRIOR_MOMENTUM", EngineId.SCALPER: "QUICK_SCALPER"}.get(c.engine)
        if (c.engine not in {EngineId.WARRIOR, EngineId.SCALPER}
                or c.intent is not IntentKind.ENTRY
                or lifecycle_owner(r.strategy_lifecycle_id) is not c.engine
                or c.lifecycle_id != r.strategy_lifecycle_id
                or c.order_id is not None or c.expected_revision is not None
                or r.side.value != "BUY" or r.order_type.value != "LIMIT"
                or r.metadata.get("source") != "autonomous-paper"
                or r.metadata.get("reason") != "ENTRY"
                or r.metadata.get("strategy_owner") != owner
                or r.quantity != r.quantity.to_integral_value()):
            raise ValueError("Only original owned whole-share equity entries supported")
        try:
            stop = Decimal(str(r.metadata.get("structural_stop")))
            risk = Decimal(str(r.metadata.get("risk_dollars")))
            valid = (stop.is_finite() and risk.is_finite() and risk > 0
                     and r.limit_price is not None and 0 < stop < r.limit_price
                     and bool(r.metadata.get("opportunity_id")))
            if c.engine is EngineId.SCALPER:
                target = Decimal(str(r.metadata.get("adaptive_target")))
                valid = valid and target.is_finite() and target > r.limit_price
        except Exception:
            valid = False
        if not valid:
            raise ValueError("Owned entry economics required")
        deadline = datetime.fromisoformat(str(r.metadata.get("chase_deadline")))
        if deadline != c.valid_until:
            raise ValueError("Candidate and placement deadline conflict")


@dataclass(frozen=True)
class EntryOutcome:
    candidate_id: str
    state: str
    result: object | None = None


@dataclass(frozen=True)
class EntryDispatch:
    state: str
    reason: str
    outcomes: tuple[EntryOutcome, ...] = ()


class GrokPaperEntries:
    """Call from a dedicated strategy worker, never the quote/protection thread.

    `authorize` must recheck the original setup, session and halt evidence at
    dispatch. The service independently requotes and applies controller/account
    gates. This adapter starts no worker and intercepts no desktop orders.
    """

    def __init__(self, suite, service, *, authorize, clock=lambda: datetime.now(UTC),
                 on_acknowledged=None):
        if not isinstance(suite, GrokSuite) or not isinstance(service, ControlledPaperTradingService):
            raise TypeError("Grok suite and controlled PAPER service required")
        if not callable(authorize):
            raise TypeError("Trusted current strategy authorization required")
        if on_acknowledged is not None and not callable(on_acknowledged):
            raise TypeError("Acknowledgement handler must be callable")
        self.suite, self.service, self.authorize, self.clock = suite, service, authorize, clock
        self.on_acknowledged = on_acknowledged
        self._lock = Lock()

    def review_and_submit(self, entries, *, engine_evidence, account_evidence,
                          entry_enabled=lambda: True):
        if not self._lock.acquire(blocking=False):
            return EntryDispatch("NOT_SENT", "AI_ENTRY_WORKER_BUSY")
        outcomes = []
        try:
            if entry_enabled() is not True:
                return EntryDispatch("NOT_SENT", "ENTRY_WORKER_STOPPED")
            entries = tuple(entries)
            if not entries:
                return EntryDispatch("NOT_SENT", "NO_CANDIDATES")
            if len(entries) > 32 or any(not isinstance(e, PreparedEntry) for e in entries):
                raise ValueError("At most 32 prepared entries required")
            ids = [e.candidate.candidate_id for e in entries]
            lifecycles = [e.candidate.lifecycle_id for e in entries]
            if len(set(ids)) != len(ids) or len(set(lifecycles)) != len(lifecycles):
                raise ValueError("Unique candidate and lifecycle identities required")
            now = self.clock()
            for e in entries:
                if (e.placement.session_id != self.service.router.session_id
                        or e.placement.order.account_id != self.service.router.account_id
                        or not 0 < (e.candidate.valid_until - now).total_seconds() <= 60):
                    raise ValueError("Current account/session and bounded deadline required")
            if any(self.authorize(e) is not True for e in entries):
                return EntryDispatch("NOT_SENT", "STRATEGY_NOT_AUTHORIZED")
            for engine in (EngineId.WARRIOR, EngineId.SCALPER):
                if entry_enabled() is not True:
                    return EntryDispatch("NOT_SENT", "ENTRY_WORKER_STOPPED")
                candidates = tuple(e.candidate for e in entries if e.candidate.engine is engine)
                if candidates:
                    self.suite.review(engine, candidates, evidence=engine_evidence.get(engine.value, {}))
            if entry_enabled() is not True:
                return EntryDispatch("NOT_SENT", "ENTRY_WORKER_STOPPED")
            advice = self.suite.coordinate(account_evidence=account_evidence)
            bound = {e.candidate.candidate_id: e for e in entries}
            now = self.clock()
            if (advice.role != "MASTER" or advice.session != self.suite.master.session
                    or not 0 <= (now - advice.observed_at).total_seconds() <= 2
                    or any(c.candidate_id not in bound or bound[c.candidate_id].candidate is not c
                           for c in advice.selected)):
                return EntryDispatch("NOT_SENT", "MASTER_BINDING_CONFLICT")
            for c in advice.selected:
                e = bound[c.candidate_id]
                if entry_enabled() is not True:
                    outcomes.append(EntryOutcome(c.candidate_id, "WORKER_STOPPED"))
                    continue
                if c.valid_until <= self.clock() or self.authorize(e) is not True:
                    outcomes.append(EntryOutcome(c.candidate_id, "AUTHORIZATION_REVOKED"))
                    continue
                if entry_enabled() is not True:
                    outcomes.append(EntryOutcome(c.candidate_id, "WORKER_STOPPED"))
                    continue
                try:
                    result = self.service.place_order(e.placement)
                    state = "SUBMITTED" if result.success else "CONTROLLER_REFUSED"
                    if result.success and self.on_acknowledged is not None:
                        try:
                            if self.on_acknowledged(e, result) is not True:
                                state = "ACKNOWLEDGED_HANDOFF_INCOMPLETE"
                        except Exception:
                            state = "ACKNOWLEDGED_HANDOFF_INCOMPLETE"
                    outcomes.append(EntryOutcome(c.candidate_id,
                        state, result))
                except Exception:
                    # Never retry ambiguous dispatch; durable controller reconciles it.
                    outcomes.append(EntryOutcome(c.candidate_id, "DISPATCH_UNCERTAIN"))
            return EntryDispatch("REVIEWED", "MASTER_SELECTED" if advice.selected else "MASTER_ABSTAINED",
                                 tuple(outcomes))
        except Exception:
            return EntryDispatch("PARTIAL" if outcomes else "NOT_SENT",
                                 "AI_ENTRY_REVIEW_UNAVAILABLE", tuple(outcomes))
        finally:
            self._lock.release()

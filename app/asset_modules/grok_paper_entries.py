"""Explicit worker-side AI entry handoff to the existing PAPER controller."""
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from app.asset_modules.controlled_paper_service import ControlledPaperTradingService
from app.asset_modules.engine_catalog import EngineId, lifecycle_owner
from app.asset_modules.grok_coordinator import Candidate, GrokSuite, IntentKind
from app.order_placement import OrderPlacementRequest


@dataclass(frozen=True)
class PreparedEntry:
    candidate: Candidate
    placement: OrderPlacementRequest

    def __post_init__(self):
        c, p = self.candidate, self.placement
        if not isinstance(c, Candidate) or not isinstance(p, OrderPlacementRequest):
            raise ValueError("Trusted immutable candidate and placement required")
        r = p.order
        if (c.engine not in {EngineId.WARRIOR, EngineId.SCALPER}
                or c.intent is not IntentKind.ENTRY
                or lifecycle_owner(r.strategy_lifecycle_id) is not c.engine
                or c.lifecycle_id != r.strategy_lifecycle_id
                or c.order_id is not None or c.expected_revision is not None
                or r.side.value != "BUY" or r.order_type.value != "LIMIT"
                or r.metadata.get("source") != "autonomous-paper"
                or r.metadata.get("reason") != "ENTRY"
                or r.quantity != r.quantity.to_integral_value()):
            raise ValueError("Only original owned whole-share equity entries supported")
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

    def __init__(self, suite, service, *, authorize, clock=lambda: datetime.now(UTC)):
        if not isinstance(suite, GrokSuite) or not isinstance(service, ControlledPaperTradingService):
            raise TypeError("Grok suite and controlled PAPER service required")
        if not callable(authorize):
            raise TypeError("Trusted current strategy authorization required")
        self.suite, self.service, self.authorize, self.clock = suite, service, authorize, clock
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
                    outcomes.append(EntryOutcome(c.candidate_id,
                        "SUBMITTED" if result.success else "CONTROLLER_REFUSED", result))
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

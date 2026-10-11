"""Bound background completions, drained on the serialized desktop runtime lane."""
from dataclasses import dataclass
from decimal import Decimal
from itertools import islice
from threading import Lock, RLock

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_entry_worker import GrokEntryWorker
from app.asset_modules.grok_paper_entries import GrokPaperEntries, PreparedEntry
from app.order_placement import OrderPlacementDecision, OrderPlacementResult
from app.strategies.warrior_momentum.autonomous_paper import AutonomousPaperExecutionBridge, lifecycle_identity
from app.strategies.warrior_momentum.forward_models import FloatProvenance, PaperSymbolAuthorization
from app.strategies.warrior_momentum.models import MomentumEntrySignal


@dataclass(frozen=True)
class RuntimeEntry:
    """Original immutable signal context, retained until the ack is reconciled."""
    prepared: PreparedEntry
    signal: MomentumEntrySignal | None = None
    float_provenance: FloatProvenance | None = None
    symbol_authorization: PaperSymbolAuthorization | None = None

    def __post_init__(self):
        if not isinstance(self.prepared, PreparedEntry):
            raise ValueError("Prepared native entry required")
        if self.prepared.candidate.engine is EngineId.WARRIOR:
            r = self.prepared.placement.order
            if (not isinstance(self.signal, MomentumEntrySignal)
                    or not isinstance(self.float_provenance, FloatProvenance)
                    or not isinstance(self.symbol_authorization, PaperSymbolAuthorization)
                    or not self.symbol_authorization.authorized
                    or self.signal.symbol != r.symbol
                    or lifecycle_identity(self.signal) != r.strategy_lifecycle_id
                    or self.signal.entry_trigger != r.limit_price
                    or self.signal.stop_price != Decimal(str(r.metadata["structural_stop"]))):
                raise ValueError("Original authorized Warrior signal context required")
        elif any(v is not None for v in (self.signal, self.float_provenance, self.symbol_authorization)):
            raise ValueError("Scalper management uses canonical order metadata")


class GrokRuntimeHandoff:
    """No queued batches or automatic retries of a buy, including uncertain buys.

    submit() binds immutable native placements. drain() and close() must run on
    the serialized owning runtime lane. AI runs only in GrokEntryWorker. A failed
    runtime adoption retains its result and blocks further batches until adopted
    or proven closed in the canonical ledger. Unknown dispatch also blocks work.
    """

    def __init__(self, suite, service, bridge, *, authorize, clock):
        if (not isinstance(bridge, AutonomousPaperExecutionBridge)
                or bridge.mode != "PAPER" or bridge.trading_service is not service):
            raise TypeError("Canonical bridge and its controlled PAPER service required")
        self.bridge = bridge
        self.worker = GrokEntryWorker(GrokPaperEntries(suite, service,
            authorize=authorize, clock=clock, on_acknowledged=self._acknowledge))
        self._lock = RLock()
        self._protection_lock = Lock()
        self._entries = {}
        self._results = {}
        self._uncertain = False
        self._uncertain_keys = set()
        self._unknown_completion = False
        self._closed = False
        self._last_reason = None

    def _acknowledge(self, entry, result):
        # This callback may register bridge ownership, never mutable Warrior
        # runtime state or GUI widgets. Native fill protection remains active.
        if self.bridge.adopt_entry_acknowledgement(entry.placement, result) is not True:
            return False
        self.reconcile_protection()
        return True

    def reconcile_protection(self):
        """Keep canonical protection available before owner-lane completion drain.

        Gateway callbacks can reenter this method during a protective submission;
        suppress that recursive call without waiting on a review/completion lock.
        """
        if not self._protection_lock.acquire(blocking=False):
            return
        try:
            self.bridge.reconcile()
            self.bridge.reconcile_protection()
        finally:
            self._protection_lock.release()

    def submit(self, entries, *, engine_evidence, account_evidence):
        with self._lock:
            if self._closed:
                return "CLOSED"
            if self._entries or self._uncertain:
                return "BUSY"
            try:
                batch = tuple(islice(entries, 33))
                if not batch or len(batch) > 32 or any(not isinstance(e, RuntimeEntry) for e in batch):
                    return "INVALID"
                ids = [e.prepared.candidate.candidate_id for e in batch]
                lifecycles = [e.prepared.candidate.lifecycle_id for e in batch]
                if len(set(ids)) != len(ids) or len(set(lifecycles)) != len(lifecycles):
                    return "INVALID"
                state = self.worker.submit(tuple(e.prepared for e in batch),
                    engine_evidence=engine_evidence, account_evidence=account_evidence)
                if state == "ACCEPTED":
                    self._entries = dict(zip(ids, batch))
                return state
            except Exception:
                return "INVALID"

    def drain(self, warrior, scalper):
        """Poll without waiting for AI; retry management adoption only."""
        with self._lock:
            report = self.worker.poll()
            if report is not None:
                self._last_reason = report.reason
                if report.reason == "ENTRY_WORKER_UNAVAILABLE":
                    self._uncertain_keys.update(self._entries)
                seen = set()
                for outcome in report.outcomes:
                    key = outcome.candidate_id
                    if key not in self._entries or key in seen:
                        self._unknown_completion = True
                        continue
                    seen.add(key)
                    if isinstance(outcome.result, OrderPlacementResult) and outcome.result.success:
                        self._results[key] = outcome.result
                    elif (outcome.state == "DISPATCH_UNCERTAIN"
                          or isinstance(outcome.result, OrderPlacementResult)
                          and outcome.result.decision is OrderPlacementDecision.GATEWAY_FAILURE):
                        self._uncertain_keys.add(key)
                self._uncertain = bool(self._uncertain_keys or self._unknown_completion)
                # Abstentions and explicit refusals did not create inventory.
                if not self._uncertain:
                    self._entries = {k: e for k, e in self._entries.items() if k in self._results}
            adopted = []
            for key, result in tuple(self._results.items()):
                entry = self._entries[key]
                try:
                    if self._proven_closed(entry, result):
                        done = True
                    elif entry.prepared.candidate.engine is EngineId.WARRIOR:
                        done = warrior is not None and warrior.adopt_acknowledged_entry(
                            entry.signal, entry.prepared.placement, result,
                            float_provenance=entry.float_provenance,
                            symbol_authorization=entry.symbol_authorization) is True
                    else:
                        done = scalper is not None and scalper.adopt_acknowledged_entry(
                            entry.prepared.placement, result) is True
                except Exception:
                    done = False
                if done:
                    adopted.append(key)
                    self._results.pop(key)
                    self._entries.pop(key)
                    self._uncertain_keys.discard(key)
            self._uncertain = bool(self._uncertain_keys or self._unknown_completion)
            return tuple(adopted)

    def recover_acknowledgement(self, candidate_id, result):
        """Accept a canonical acknowledgement from durable reconciliation only.

        No order is submitted and an absent order is never treated as proof
        of a failed buy. Unbound/duplicate completion corruption stays latched.
        """
        with self._lock:
            entry = self._entries.get(candidate_id)
            if (entry is None or candidate_id not in self._uncertain_keys
                    or not isinstance(result, OrderPlacementResult) or not result.success):
                return False
            try:
                if self.bridge.adopt_entry_acknowledgement(entry.prepared.placement, result) is not True:
                    return False
            except Exception:
                return False
            self._results[candidate_id] = result
            return True

    def _proven_closed(self, entry, result):
        if self.bridge.adopt_entry_acknowledgement(entry.prepared.placement, result) is not True:
            return False
        order = self.bridge.order_book.get(result.broker_order_id)
        if order is None or not order.is_terminal:
            return False
        r = entry.prepared.placement.order
        remaining = sum((o.filled_quantity * (1 if o.request.side.value == "BUY" else -1)
            for o in self.bridge.order_book.history()
            if o.symbol == r.symbol and o.request.strategy_lifecycle_id == r.strategy_lifecycle_id), Decimal(0))
        return remaining == 0

    def close(self, warrior, scalper):
        """Nonblocking stop; False keeps the owning runtime available to drain."""
        with self._lock:
            self._closed = True
            stopped = self.worker.close(timeout=0)
            self.drain(warrior, scalper)
            return stopped and not self._entries and not self._uncertain

    def request_stop(self):
        """Revoke worker permission without touching runtime state."""
        with self._lock:
            self._closed = True
            return self.worker.close(timeout=0)

    def snapshot(self):
        with self._lock:
            return {"closed": self._closed, "pending_entries": len(self._entries),
                    "pending_acknowledgements": len(self._results),
                    "reconciliation_required": self._uncertain,
                    "last_reason": self._last_reason}

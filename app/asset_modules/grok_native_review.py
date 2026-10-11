"""Bounded native equity review receipts and expiring strategy authorization."""
from dataclasses import dataclass, replace
from datetime import datetime
from itertools import islice
from threading import RLock

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_paper_entries import PreparedEntry
from app.asset_modules.grok_runtime_handoff import GrokRuntimeHandoff, RuntimeEntry
from app.strategies.warrior_momentum.autonomous_paper import lifecycle_identity


@dataclass(frozen=True)
class CurrentEntryAuthorization:
    """Trusted runtime evidence, never an AI-provided authorization.

    A producer must refresh this on its serialized lane from current setup,
    tradability, halt and session checks. A snapshot expires after two seconds
    or at valid_until, whichever is earlier. False checks revoke dispatch.
    """
    lifecycle_id: str
    observed_at: datetime
    valid_until: datetime
    setup_confirmed: bool
    tradable: bool
    halt_clear: bool
    session_allowed: bool
    execution_permitted: bool

    def __post_init__(self):
        if (not isinstance(self.lifecycle_id, str) or not self.lifecycle_id
                or any(not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None
                       for t in (self.observed_at, self.valid_until))
                or not 0 < (self.valid_until - self.observed_at).total_seconds() <= 60
                or any(type(v) is not bool for v in (self.setup_confirmed, self.tradable,
                    self.halt_clear, self.session_allowed, self.execution_permitted))):
            raise ValueError("Bounded typed current strategy authorization required")

    def allows(self, now):
        return (0 <= (now - self.observed_at).total_seconds() <= 2
                and now < self.valid_until
                and all((self.setup_confirmed, self.tradable, self.halt_clear,
                         self.session_allowed, self.execution_permitted)))


@dataclass(frozen=True)
class NativeReviewRequest:
    entry: RuntimeEntry
    authorization: CurrentEntryAuthorization

    def __post_init__(self):
        if (not isinstance(self.entry, RuntimeEntry)
                or not isinstance(self.authorization, CurrentEntryAuthorization)
                or self.authorization.lifecycle_id != self.entry.prepared.candidate.lifecycle_id):
            raise ValueError("Authorization must bind the exact native lifecycle")


@dataclass(frozen=True)
class NativeReviewReceipt:
    state: str
    reason: str
    candidate_ids: tuple[str, ...] = ()

    def __bool__(self):
        raise TypeError("Review receipt requires an explicit pending/state check")

    @property
    def pending(self):
        return self.state == "PENDING_REVIEW"


class NativeGrokReview:
    """Explicit producer boundary, with one batch and no automatic retries.

    Native pipelines must handle pending separately from submission. This
    adapter does not install itself in the desktop or bypass the native gates.
    drain() and take_resolutions() belong to the serialized owning runtime lane.
    No model call or order dispatch holds the authorization lock.
    """
    def __init__(self, suite, service, bridge, *, clock):
        self.bridge, self.clock = bridge, clock
        self._lock = RLock()
        self._grants = {}
        self._entries = {}
        self._resolution_backlog = {}
        self._closed = False
        self.handoff = GrokRuntimeHandoff(suite, service, bridge,
            authorize=self._authorize, clock=clock, retain_resolutions=True)

    def prepare(self, signal, shares, risk_dollars, *, authorization, evidence,
                float_provenance=None, symbol_authorization=None, **native_options):
        """Prepare from a fresh native signal, without dispatching or reserving.

        Invalid producer evidence is refused before invoking the native builder.
        The caller must keep its native ownership claim while receipt.pending.
        """
        with self._lock:
            if self._closed:
                raise ValueError("Review producer closed")
        if (not isinstance(authorization, CurrentEntryAuthorization)
                or authorization.lifecycle_id != lifecycle_identity(signal)
                or authorization.allows(self.clock()) is not True):
            raise ValueError("Current confirmed native signal required")
        options = dict(native_options)
        deadline = options.get("opportunity_deadline")
        options["opportunity_deadline"] = min(deadline, authorization.valid_until) if deadline is not None else authorization.valid_until
        preparation = self.bridge.prepare_entry_decision(signal, shares, risk_dollars, **options)
        if preparation.placement is None:
            return preparation
        prepared = PreparedEntry.from_placement(preparation.placement, evidence=evidence)
        entry = RuntimeEntry(prepared, signal, float_provenance, symbol_authorization) if prepared.candidate.engine is EngineId.WARRIOR else RuntimeEntry(prepared)
        return NativeReviewRequest(entry, authorization)

    def submit(self, requests, *, engine_evidence, account_evidence):
        """PENDING_REVIEW means bound review only; never a broker acknowledgement."""
        try:
            batch = tuple(islice(requests, 33))
        except Exception:
            return NativeReviewReceipt("NOT_SENT", "INVALID")
        with self._lock:
            if self._closed:
                return NativeReviewReceipt("NOT_SENT", "CLOSED")
            if self._entries:
                return NativeReviewReceipt("NOT_SENT", "BUSY")
            if not batch or len(batch) > 32 or any(not isinstance(r, NativeReviewRequest) for r in batch):
                return NativeReviewReceipt("NOT_SENT", "INVALID")
            ids = tuple(r.entry.prepared.candidate.candidate_id for r in batch)
            if len(set(ids)) != len(ids):
                return NativeReviewReceipt("NOT_SENT", "INVALID")
            now = self.clock()
            if any(r.authorization.allows(now) is not True for r in batch):
                return NativeReviewReceipt("NOT_SENT", "AUTHORIZATION_NOT_CURRENT")
            self._entries = {key: r.entry for key, r in zip(ids, batch)}
            self._grants = {key: r.authorization for key, r in zip(ids, batch)}
        # Do not hold a producer lock while the handoff starts a worker. The
        # worker may immediately ask to authorize its immutable placement.
        state = self.handoff.submit(tuple(r.entry for r in batch),
            engine_evidence=engine_evidence, account_evidence=account_evidence)
        if state != "ACCEPTED":
            with self._lock:
                self._entries.clear()
                self._grants.clear()
            return NativeReviewReceipt("NOT_SENT", state)
        return NativeReviewReceipt("PENDING_REVIEW", "AI_REVIEW_PENDING", ids)

    def refresh(self, candidate_id, authorization):
        """Refresh or revoke exactly the retained lifecycle; cannot alter orders."""
        with self._lock:
            entry = self._entries.get(candidate_id)
            previous = self._grants.get(candidate_id)
            if (self._closed or entry is None or not isinstance(authorization, CurrentEntryAuthorization)
                    or authorization.lifecycle_id != entry.prepared.candidate.lifecycle_id
                    or authorization.observed_at < previous.observed_at
                    or authorization.valid_until > entry.prepared.candidate.valid_until
                    or not 0 <= (self.clock() - authorization.observed_at).total_seconds() <= 2):
                return False
            self._grants[candidate_id] = authorization
            return True

    def _authorize(self, prepared):
        with self._lock:
            key = prepared.candidate.candidate_id
            entry, grant = self._entries.get(key), self._grants.get(key)
            return (not self._closed and entry is not None and entry.prepared is prepared
                    and grant is not None and grant.allows(self.clock()) is True)

    def drain(self, warrior, scalper):
        return self.handoff.drain(warrior, scalper)

    def apply_resolutions(self, warrior, scalper, *, at):
        """Owner-lane pump; retain receipts until native bookkeeping succeeds."""
        self.handoff.drain(warrior, scalper)
        fresh = self.handoff.take_resolutions()
        with self._lock:
            for resolution in fresh:
                self._resolution_backlog[resolution.entry.prepared.candidate.candidate_id] = resolution
            pending = tuple(self._resolution_backlog.items())
        applied = []
        for key, resolution in pending:
            try:
                if (resolution.state == "ACKNOWLEDGED"
                        and self.handoff._proven_closed(resolution.entry, resolution.result)):
                    resolution = replace(resolution, state="POSITION_CLOSED")
                runtime = warrior if resolution.entry.prepared.candidate.engine is EngineId.WARRIOR else scalper
                done = runtime is not None and runtime.resolve_native_review(resolution, at=at) is True
            except Exception:
                done = False
            if done:
                with self._lock:
                    self._resolution_backlog.pop(key, None)
                    self._entries.pop(key, None)
                    self._grants.pop(key, None)
                applied.append(key)
        return tuple(applied)

    def take_resolutions(self):
        fresh = self.handoff.take_resolutions()
        with self._lock:
            resolutions = (*self._resolution_backlog.values(), *fresh)
            self._resolution_backlog.clear()
            for resolution in resolutions:
                key = resolution.entry.prepared.candidate.candidate_id
                self._entries.pop(key, None)
                self._grants.pop(key, None)
        return resolutions

    def pending_states(self):
        states = self.handoff.entry_states()
        with self._lock:
            states.update({key: "NATIVE_RECEIPT_PENDING" for key in self._resolution_backlog})
        return states

    def request_stop(self):
        with self._lock:
            self._closed = True
            self._grants.clear()
        return self.handoff.request_stop()


    def close(self, warrior, scalper):
        """Revoke immediately; retain producer state until receipts are consumed."""
        self.request_stop()
        done = self.handoff.close(warrior, scalper)
        with self._lock:
            return done and not self._entries

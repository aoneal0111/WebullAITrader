"""Native desktop callbacks; no model waits or activation configuration."""
from datetime import datetime
from decimal import Decimal

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_native_review import CurrentEntryAuthorization, NativeGrokReview, NativeReviewReceipt, NativeReviewRequest
from app.strategies.warrior_momentum.autonomous_paper import lifecycle_identity
from app.strategies.warrior_momentum.features import BAR_INTERVAL
from app.strategies.warrior_momentum.quick_scalper import ScalpDecision


class DesktopGrokProducer:
    """Offers only native final-gate signals and refreshes their bound leases.

    The callbacks must be bound exclusively to native final entry/assessment
    branches. Hard setup, tradability, halt and session checks are inherited
    from those branches, not invented by the model. No provider is invoked on
    this owner lane; NativeGrokReview submits to its bounded background worker.
    """
    def __init__(self, review, *, permitted):
        if not isinstance(review, NativeGrokReview) or not callable(permitted):
            raise TypeError("Native PAPER review and local permission source required")
        self.review, self.permitted = review, permitted
        self._requests = {}
        review.bind_execution_permission(self._permitted)

    def _permitted(self):
        try:
            return (self.permitted() is True and self.review.bridge.enabled
                    and self.review.bridge.mode == "PAPER")
        except Exception:
            return False

    def _offer(self, signal, shares, risk, at, *, float_provenance=None, symbol_authorization=None):
        if not self._permitted():
            return NativeReviewReceipt("NOT_SENT", "EXECUTION_NOT_PERMITTED")
        identity = lifecycle_identity(signal)
        if any(r.entry.prepared.placement.order.symbol == signal.symbol for r in self._requests.values()):
            return NativeReviewReceipt("NOT_SENT", "BUSY")
        deadline = signal.timestamp + BAR_INTERVAL
        now = self.review.clock()
        if (not isinstance(at, datetime) or at.tzinfo is None
                or signal.timestamp > at or not 0 <= (now - at).total_seconds() <= 2
                or deadline <= now):
            return NativeReviewReceipt("NOT_SENT", "NATIVE_SIGNAL_EXPIRED_OR_NOT_CURRENT")
        authorization = CurrentEntryAuthorization(identity, at, deadline, True, True, True, True, True)
        evidence = {"source": "NATIVE_FINAL_ENTRY_GATES", "evaluated_at": at.isoformat(),
            "native_signal_timestamp": signal.timestamp.isoformat(), "session": signal.session,
            "setup": str(getattr(signal, "setup_type", "QUICK_SCALP_MOMENTUM")),
            "target_levels": [str(p) for p in getattr(signal, "target_levels", ())]}
        request = self.review.prepare(signal, shares, risk, authorization=authorization,
            evidence=evidence, float_provenance=float_provenance, symbol_authorization=symbol_authorization)
        if not isinstance(request, NativeReviewRequest):
            return NativeReviewReceipt("NOT_SENT", request.decision.reason.value)
        receipt = self.review.submit([request], engine_evidence={request.entry.prepared.candidate.engine.value: evidence},
            account_evidence={"mode": "PAPER", "risk_sizing": "NATIVE_APPROVED",
                "account_validation": "CONTROLLED_SERVICE_RECHECKS_AT_DISPATCH"})
        if receipt.pending:
            self._requests[receipt.candidate_ids[0]] = request
        return receipt

    def warrior_offer(self, signal, shares, risk, *, float_provenance, symbol_authorization, evaluated_at):
        return self._offer(signal, shares, risk, evaluated_at,
            float_provenance=float_provenance, symbol_authorization=symbol_authorization)

    def scalper_offer(self, intent, shares, risk):
        return self._offer(intent, shares, risk, intent.timestamp)

    def _refresh(self, engine, symbol, at, *, matches):
        refreshed = False
        for key, request in tuple(self._requests.items()):
            entry = request.entry
            if entry.prepared.candidate.engine is not engine or entry.prepared.placement.order.symbol != symbol:
                continue
            deadline = entry.prepared.candidate.valid_until
            if at.tzinfo is None or deadline <= at:
                continue  # Never extend the original expiry to construct a fresh lease.
            valid = matches(entry) is True
            authorization = CurrentEntryAuthorization(entry.prepared.candidate.lifecycle_id,
                at, deadline, valid, True, True, valid, self._permitted())
            refreshed = self.review.refresh(key, authorization) or refreshed
        return refreshed

    def warrior_refresh(self, signal, *, evaluated_at):
        def matches(entry):
            original = entry.signal
            return (lifecycle_identity(signal) == entry.prepared.candidate.lifecycle_id
                and signal.stop_price == original.stop_price and signal.session == original.session
                and (signal.structural_entry_trigger or signal.entry_trigger)
                    == (original.structural_entry_trigger or original.entry_trigger))
        return self._refresh(EngineId.WARRIOR, signal.symbol, evaluated_at, matches=matches)

    def scalper_refresh(self, snapshot, assessed):
        def matches(entry):
            r = entry.prepared.placement.order
            return (assessed.decision is ScalpDecision.EXECUTABLE
                and assessed.opportunity.lifecycle_id == entry.prepared.candidate.lifecycle_id
                and snapshot.structural_stop == Decimal(str(r.metadata["structural_stop"]))
                and snapshot.generation_id == r.metadata.get("generation_id"))
        return self._refresh(EngineId.SCALPER, snapshot.symbol, snapshot.decision_at, matches=matches)

    def drain(self, warrior, scalper, *, at):
        applied = self.review.apply_resolutions(warrior, scalper, at=at)
        for key in applied:
            self._requests.pop(key, None)
        return applied

    def request_stop(self):
        return self.review.request_stop()

    def close(self, warrior, scalper, *, at):
        self.request_stop()
        self.drain(warrior, scalper, at=at)
        return self.review.close(warrior, scalper) and not self._requests

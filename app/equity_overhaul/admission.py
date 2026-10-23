"""Desktop BUY admission. Research results never grant order authority.

The reviewed release registry is intentionally empty: no equity replacement has
passed Atlas execution, later-period, cost and forward-paper validation yet.
No environment variable, Grok response or metadata can add a registry entry.
"""
from dataclasses import dataclass
import logging
from threading import RLock

from app.order_placement import (AcknowledgementState, NormalizedOrderStatus,
                                 OrderPlacementDecision, OrderPlacementResult)
from app.performance_diagnostics import performance_diagnostics

POLICY_VERSION = 'EQUITY_QUALIFICATION_V1'
CANDIDATES = ('ORB5_LONG_V1', 'NOISE_BAND_LONG_V1', 'VWAP_RECLAIM_SCALP_V1')


@dataclass(frozen=True)
class Promotion:
    policy_id: str
    code_fingerprint: str
    configuration_fingerprint: str
    validation_report_sha256: str

    def __post_init__(self):
        if self.policy_id not in CANDIDATES:
            raise ValueError('legacy or unknown policies cannot be promoted')
        for value in (self.code_fingerprint, self.configuration_fingerprint, self.validation_report_sha256):
            if len(value)!=64 or any(x not in '0123456789abcdef' for x in value):
                raise ValueError('exact reviewed SHA-256 fingerprints required')


# Adding a release approval requires a code-reviewed change with reconciled
# evidence, including matching rule/config hashes. An author's report is not it.
RELEASE_PROMOTIONS: tuple[Promotion, ...] = ()


class EquityQualificationPolicy:
    def __init__(self, promotions=RELEASE_PROMOTIONS):
        self._promotions = tuple(promotions)
        if any(not isinstance(p,Promotion) for p in self._promotions):
            raise ValueError('typed release promotions required')
        if len({p.policy_id for p in self._promotions})!=len(self._promotions):
            raise ValueError('duplicate release promotion')

    def permits(self, metadata):
        if not isinstance(metadata,dict):return False
        identity = metadata.get('strategy_policy_version')
        return any(identity==p.policy_id
                   and metadata.get('strategy_code_fingerprint')==p.code_fingerprint
                   and metadata.get('strategy_configuration_fingerprint')==p.configuration_fingerprint
                   for p in self._promotions)

    def status(self):
        return dict(policy=POLICY_VERSION, entry_mode='QUALIFIED_ONLY',
                    approved_policies=[p.policy_id for p in self._promotions],
                    replacement_candidates=list(CANDIDATES),
                    legacy_entries='QUARANTINED', protective_sells='AVAILABLE',
                    reason='NO_QUALIFIED_EQUITY_STRATEGY' if not self._promotions else 'REVIEWED_RELEASE_REGISTRY')


def production_policy():
    return EquityQualificationPolicy()


class QualifiedEquityTradingService:
    """Mandatory desktop service wrapper, independent of controller/AI flags.

    SELLs and cancellations use the original service. BUYs, including add-ons,
    re-arms, prepared/Grok dispatches and replacements, need release approval.
    This is software authority, not a promise that an approved trade will win.
    """
    def __init__(self, service, policy):
        self.service, self.policy = service, policy
        self.last_reason = 'NOT_EVALUATED'
        self._lock = RLock()

    def __getattr__(self,name):
        return getattr(self.service,name)

    def place_order(self, placement):
        if placement.order.side.value!='BUY':
            return self.service.place_order(placement)
        with self._lock:
            if not self.policy.permits(dict(placement.order.metadata)):
                self.last_reason='STRATEGY_NOT_QUALIFIED'
                performance_diagnostics.record_entry_funnel(symbol=placement.order.symbol,
                    stage='STRATEGY_QUALIFICATION',outcome='REJECTED',reason=self.last_reason)
                return OrderPlacementResult(placement.order.request_id, placement.order.client_order_id,'',
                    AcknowledgementState.NOT_SENT, NormalizedOrderStatus.NOT_SUBMITTED,
                    OrderPlacementDecision.ORDER_REJECTED, self.last_reason,(),
                    {'source':'equity-qualification','qualification_reason':self.last_reason})
            return self.service.place_order(placement)

    def cancel_order(self,request):
        return self.service.cancel_order(request)

    def quarantine_working_buys(self,order_book,factory):
        """Cancel inherited unqualified BUYs before market delivery can fill them.

        Partial inventory and every SELL remain for normal reconciliation.
        Return False on an uncertain cancellation so entry readiness stays shut.
        """
        success=True
        for order in order_book.open_orders():
            if order.request.side.value!='BUY' or self.policy.permits(dict(order.request.metadata)):
                continue
            request=factory.create_cancellation_request(order.order_id,order.request.client_order_id,
                source='equity-strategy-quarantine')
            try:
                result=self.service.cancel_order(request)
                if result is None or result.success is not True:success=False
            except Exception:
                success=False
        logging.getLogger(__name__).warning(
            'equity_strategy_admission mode=QUALIFIED_ONLY approved_policies=%s inherited_buys_cancelled=%s protective_sells=AVAILABLE',
            len(self.policy.status()['approved_policies']),success)
        return success

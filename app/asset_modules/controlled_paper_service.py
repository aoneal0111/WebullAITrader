"""Trusted desktop equity entry gate; sells/cancellations retain their service."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from threading import RLock

from app.asset_modules.admission_controller import EntryReservation
from app.asset_modules.engine_catalog import EngineId, lifecycle_owner
from app.asset_modules.paper_controller_router import ControllerEntryRefused, PaperControllerRouter
from app.configuration.models import PaperSymbolAuthorizationMode
from app.order_placement import (AcknowledgementState, NormalizedOrderStatus, OrderPlacementDecision,
                                 OrderPlacementResult)
from app.performance_diagnostics import performance_diagnostics
from app.strategies.warrior_momentum.risk import size_execution_position


class ControlledPaperTradingService:
    """Internal strategy service only, never a model-facing order endpoint.

    Existing upstream session/halt/setup authorization is preserved. Final
    executable bid/ask and account risk are checked again at dispatch. No AI
    call is involved; the only external request is the existing quote source.
    """

    def __init__(self, service, controller, gateway, *, account_id, session_id,
                 account_source, quote_source, risk_config, entry_ready, clock=None):
        self.service = service
        self.account_source = account_source
        self.quote_source = quote_source
        self.risk_config = risk_config
        self.entry_ready = entry_ready
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()
        self._quote = None
        self.last_reason = "NOT_EVALUATED"
        self.router = PaperControllerRouter(controller, gateway, account_id=account_id,
            session_id=session_id, authorize=self._authorize, clock=self.clock)

    def _authorize(self, request):
        if self.entry_ready() is not True:
            self.last_reason = "BRIDGE_NOT_READY"
            return False
        quote = self._quote
        now = self.clock()
        if quote is None or quote.symbol != request.instrument:
            self.last_reason = "EXECUTION_QUOTE_UNAVAILABLE"
            return False
        try:
            if any(not value.is_finite() or value <= 0 for value in (quote.bid, quote.ask)):
                raise ValueError("invalid quote")
            if quote.ask < quote.bid or quote.ask > request.entry_limit or quote.bid <= request.structural_stop:
                self.last_reason = "ENTRY_NOT_EXECUTABLE"
                return False
            for stamp in (quote.bid_timestamp, quote.ask_timestamp):
                if stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= 2:
                    self.last_reason = "PROVIDER_QUOTE_NOT_CURRENT"
                    return False
            account = self.account_source()
            if account is None:
                self.last_reason = "ACCOUNT_UNAVAILABLE"
                return False
            mode = account.symbol_authorization_mode
            symbol_allowed = request.instrument in account.allowed_symbols or (
                request.engine is EngineId.WARRIOR and mode in {
                    PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR,
                    PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR_AND_QUICK_SCALPER}) or (
                request.engine is EngineId.SCALPER and mode is PaperSymbolAuthorizationMode.DYNAMIC_WARRIOR_AND_QUICK_SCALPER)
            risk = size_execution_position(symbol=request.instrument, reference_price=request.entry_limit,
                risk_per_share=request.entry_limit - request.structural_stop, account_equity=account.equity,
                buying_power=account.buying_power, allowed_symbols=account.allowed_symbols,
                symbol_authorized=symbol_allowed, existing_exposure=account.existing_exposure,
                exposure_limit=account.exposure_limit, risk_engine_approved=account.risk_engine_approved,
                broker_restriction=account.broker_restriction, config=self.risk_config)
            allowed = risk.approved and request.quantity <= risk.shares
            self.last_reason = "AUTHORIZED" if allowed else "RISK_NOT_AUTHORIZED"
            return allowed
        except (ValueError, TypeError, AttributeError, ArithmeticError):
            self.last_reason = "AUTHORIZATION_DATA_INVALID"
            return False

    def _refused(self, placement, reason):
        self.last_reason = reason
        performance_diagnostics.record_entry_funnel(symbol=placement.order.symbol,
            stage="PAPER_AUTHORIZATION", outcome="REJECTED", reason="CONTROLLER_" + reason)
        return OrderPlacementResult(placement.order.request_id, placement.order.client_order_id, "",
            AcknowledgementState.NOT_SENT, NormalizedOrderStatus.NOT_SUBMITTED,
            OrderPlacementDecision.ORDER_REJECTED, "PAPER_CONTROLLER: " + reason, (),
            {"source": "atlas-paper-controller", "controller_reason": reason})

    def place_order(self, placement):
        if placement.order.side.value != "BUY":
            # Protection must survive admission/recovery/quote failures.
            return self.service.place_order(placement)
        with self._lock:
            r = placement.order
            engine = lifecycle_owner(r.strategy_lifecycle_id)
            if (r.metadata.get("source") != "autonomous-paper" or engine not in {EngineId.WARRIOR, EngineId.SCALPER}
                    or r.metadata.get("reason") not in {"ENTRY", "ENTRY_REPLACEMENT"}):
                return self._refused(placement, "UNSUPPORTED_ENTRY_MUTATION")
            self._quote = None
            try:
                self.router.reconcile()
                self._quote = self.quote_source(r.symbol)
                if self._quote is None:
                    return self._refused(placement, "EXECUTION_QUOTE_UNAVAILABLE")
                now = self.clock()
                metadata = dict(r.metadata)
                deadline = metadata.get("chase_deadline") or metadata.get("opportunity_deadline")
                expiry = datetime.fromisoformat(str(deadline)) if deadline else now + timedelta(seconds=60)
                # Metadata provenance is part of immutable command identity.
                signature = sha256(json.dumps(metadata, sort_keys=True, default=str).encode()).hexdigest()
                sequence = str(metadata.get("replacement_sequence", "0"))
                command = sha256((r.strategy_lifecycle_id + "|" + sequence).encode()).hexdigest()
                with localcontext() as ctx:
                    ctx.prec = 100
                    stop = Decimal(str(metadata["structural_stop"]))
                    reservation = EntryReservation(command_id=command, account_id=r.account_id,
                        engine=engine, lifecycle_id=r.strategy_lifecycle_id, instrument=r.symbol,
                        policy_version="DESKTOP_PAPER_ENTRY_V1:" + signature, side="BUY",
                        quantity=r.quantity, entry_limit=r.limit_price, structural_stop=stop, multiplier=Decimal(1),
                        capital=r.quantity * r.limit_price,
                        loss_budget=max(Decimal(str(metadata["risk_dollars"])), (r.limit_price - stop) * r.quantity),
                        quote_at=min(self._quote.bid_timestamp, self._quote.ask_timestamp), expires_at=expiry)
                result = self.router.submit_placement(reservation, placement, self.service.place_order)
                self.last_reason = "SUBMITTED" if result.success else "PLACEMENT_REFUSED"
                return result
            except ControllerEntryRefused as exc:
                reason = self.last_reason if str(exc) == "ENTRY_NOT_AUTHORIZED" else str(exc)
                return self._refused(placement, reason)
            except Exception:
                # Never turn uncertainty or a corrupt ledger into a bypass.
                return self._refused(placement, "CONTROLLER_UNAVAILABLE")
            finally:
                self._quote = None

    def cancel_order(self, request):
        return self.service.cancel_order(request)

    def reconcile(self):
        with self._lock:
            return self.router.reconcile()

    def status(self):
        with self._lock:
            return {"enabled": True, "campaign_id": self.router.campaign_id,
                    "last_reason": self.last_reason,
                    "ledger_path": str(self.router.controller.path),
                    **self.router.controller.snapshot(self.router.account_id)}

"""Explicit, dedicated PAPER stock routing; not installed in desktop composition."""
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from hashlib import sha256
from threading import RLock

from app.asset_modules.admission_controller import Admission, EntryReservation
from app.asset_modules.engine_catalog import EngineId
from app.order_placement import OrderPlacementRequest, OrderRequestModel, OrderSide, OrderType, TimeInForce


class ControllerEntryRefused(RuntimeError):
    pass


class PaperControllerRouter:
    """One trusted ingress per account; existing strategy gates remain mandatory.

    The supplied authorize callback must perform deterministic session, quote,
    halt, risk and protection-readiness checks. It must return exactly True.
    This adapter does not implement or replace position protection.
    """

    def __init__(self, controller, gateway, *, account_id, session_id, authorize, clock=None):
        if not callable(authorize):
            raise TypeError("Trusted entry authorization callback required")
        self.controller = controller
        self.gateway = gateway
        self.account_id = account_id
        self.session_id = session_id
        self.authorize = authorize
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()
        self.campaign_id = gateway.paper_campaign_id
        with gateway.controller_reconciliation(account_id):
            pass

    @staticmethod
    def client_id(command_id):
        return "atlas-controller-" + sha256(command_id.encode()).hexdigest()

    def _commands(self):
        rows = []
        after = ""
        while True:
            page = self.controller.recovery_commands(self.account_id, after_command_id=after, limit=1000)
            rows.extend(page)
            if len(page) < 1000:
                return rows
            after = page[-1]["request"]["command_id"]

    def _unmanaged_exposure(self, orders):
        owned = {row["request"]["lifecycle_id"] for row in self._commands()}
        net = defaultdict(Decimal)
        with localcontext() as ctx:
            ctx.prec = 100
            for order in orders:
                lifecycle = order.request.strategy_lifecycle_id
                if lifecycle in owned:
                    continue
                if not order.is_terminal:
                    return True
                net[(order.symbol, lifecycle)] += order.filled_quantity * (1 if order.request.side.value == "BUY" else -1)
        return any(net.values())

    def _claim(self, request):
        if (request.account_id != self.account_id or request.engine not in {EngineId.WARRIOR, EngineId.SCALPER}
                or request.side != "BUY" or request.multiplier != 1 or request.quantity != request.quantity.to_integral_value()):
            raise ValueError("Router supports owned long whole-share equity entries only")
        if self.authorize(request) is not True:
            return Admission("REJECTED", "ENTRY_NOT_AUTHORIZED")
        with self.gateway.controller_reconciliation(self.account_id, campaign_id=self.campaign_id) as orders:
            if self._unmanaged_exposure(orders):
                return Admission("REJECTED", "UNMANAGED_PAPER_EXPOSURE")
            if any(o.request.strategy_lifecycle_id == request.lifecycle_id
                   and o.request.client_order_id != self.client_id(request.command_id)
                   and o.request.execution_reason in {"ENTRY", "ENTRY_REPLACEMENT"}
                   and (not o.is_terminal or o.filled_quantity != 0) for o in orders):
                return Admission("REJECTED", "LIFECYCLE_ALREADY_USED")
        admission = self.controller.reserve(request, now=self.clock())
        if admission.state != "RESERVED":
            return admission
        if self.authorize(request) is not True:
            self.controller.abandon_unsubmitted(request.command_id, request.engine)
            return Admission("ABANDONED", "ENTRY_NOT_AUTHORIZED")
        if not self.controller.claim_dispatch(request.command_id, request.engine, now=self.clock()):
            return Admission("NOT_DISPATCHED")
        return Admission("SUBMITTING", "DISPATCH_CLAIMED")

    def submit(self, request: EntryReservation):
        with self._lock:
            admission = self._claim(request)
            if admission.reason != "DISPATCH_CLAIMED":
                return admission
            order = OrderRequestModel(
                request_id=self.client_id(request.command_id), account_id=self.account_id,
                symbol=request.instrument, side=OrderSide.BUY, order_type=OrderType.LIMIT,
                quantity=request.quantity, limit_price=request.entry_limit, stop_price=None,
                time_in_force=TimeInForce.DAY, client_order_id=self.client_id(request.command_id),
                strategy_lifecycle_id=request.lifecycle_id,
                metadata={"source": "atlas-paper-controller", "reason": "ENTRY",
                          "controller_command_id": request.command_id, "policy_version": request.policy_version,
                          "structural_stop": str(request.structural_stop), "chase_deadline": request.expires_at.isoformat()},
            )
            ack = self.gateway.place_order(OrderPlacementRequest(self.session_id, order))
            if ack.accepted:
                self.controller.acknowledge(request.command_id, request.engine, ack.broker_order_id)
                return Admission("ACKNOWLEDGED")
            return Admission("SUBMITTING", "BROKER_REJECTION_REQUIRES_RECONCILIATION")

    def submit_placement(self, reservation, placement, submit):
        """Preserve strategy metadata and use the existing TradingService gates."""
        r = placement.order
        if (placement.session_id != self.session_id or r.account_id != self.account_id
                or r.symbol != reservation.instrument or r.side.value != "BUY"
                or r.order_type.value != "LIMIT" or r.quantity != reservation.quantity
                or r.limit_price != reservation.entry_limit or r.strategy_lifecycle_id != reservation.lifecycle_id
                or Decimal(str(r.metadata.get("structural_stop"))) != reservation.structural_stop
                or r.metadata.get("reason") not in {"ENTRY", "ENTRY_REPLACEMENT"}):
            raise ValueError("Placement and reservation identity conflict")
        with self._lock:
            admission = self._claim(reservation)
            if admission.reason != "DISPATCH_CLAIMED":
                raise ControllerEntryRefused(admission.reason or admission.state)
            routed = replace(placement, order=replace(r,
                client_order_id=self.client_id(reservation.command_id),
                metadata={**dict(r.metadata), "controller_command_id": reservation.command_id,
                          "policy_version": reservation.policy_version,
                          "chase_deadline": reservation.expires_at.isoformat()}))
            result = submit(routed)
            if result.success:
                self.controller.acknowledge(reservation.command_id, reservation.engine, result.broker_order_id)
            return result

    def reconcile(self):
        results = {}
        with self._lock, self.gateway.controller_reconciliation(self.account_id, campaign_id=self.campaign_id) as orders:
            for row in self._commands():
                request = row["request"]
                command = request["command_id"]
                if row["state"] == "RESERVED":
                    results[command] = "UNSUBMITTED"
                    continue
                entry = [o for o in orders if o.request.client_order_id == self.client_id(command)]
                if len(entry) != 1:
                    results[command] = "UNCERTAIN"
                    continue
                entry = entry[0]
                r = entry.request
                if (r.strategy_lifecycle_id != request["lifecycle_id"] or r.symbol != request["instrument"]
                        or r.side.value != request["side"] or r.order_type.value != "LIMIT"
                        or r.quantity != Decimal(request["quantity"]) or r.limit_price != Decimal(request["entry_limit"])
                        or r.structural_stop_price != Decimal(request["structural_stop"])
                        or r.metadata.get("policy_version") != request["policy_version"]
                        or r.metadata.get("controller_command_id") != command
                        or r.execution_reason not in {"ENTRY", "ENTRY_REPLACEMENT"}
                        or r.entry_valid_until != datetime.fromisoformat(request["expires_at"])
                        or (row["broker_order_id"] and row["broker_order_id"] != entry.order_id)):
                    results[command] = "IDENTITY_CONFLICT"
                    continue
                lifecycle = [o for o in orders if o.request.strategy_lifecycle_id == request["lifecycle_id"]]
                if any(o.symbol != request["instrument"] for o in lifecycle):
                    results[command] = "IDENTITY_CONFLICT"
                    continue
                engine = EngineId(request["engine"])
                if row["state"] == "SUBMITTING":
                    self.controller.acknowledge(command, engine, entry.order_id)
                with localcontext() as ctx:
                    ctx.prec = 100
                    net = sum((o.filled_quantity * (1 if o.request.side.value == "BUY" else -1) for o in lifecycle), Decimal(0))
                if net == 0 and all(o.is_terminal for o in lifecycle):
                    self.controller.reconcile_flat(command, engine, orders_terminal=True,
                        remaining_quantity=net, reference="paper-order:" + entry.order_id)
                    results[command] = "CLOSED"
                else:
                    results[command] = "HELD"
        return results

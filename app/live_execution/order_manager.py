from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.broker_protocol.protocol import Broker
from app.broker_protocol.models import BrokerFill,BrokerOrderStatus as LiveOrderStatus
from app.authorization.models import ValidatedExecutionIntent
from app.authorization.registry import AuthorizationRegistry,consume
from app.live_execution.events import ExecutionEventLog, ExecutionEventType, append_event
from app.live_execution.models import LocalOrder,LocalPortfolioState,ReplacementRequest
from app.live_execution.order_translation import translate_order, validate_broker_order_request
from app.live_execution.portfolio_state import transition, upsert_order
from app.live_execution.recovery import DurableExecutionJournal, MutationState


def submit(intent: ValidatedExecutionIntent, broker: Broker, state: LocalPortfolioState,
           log: ExecutionEventLog, timestamp: datetime,authorization_registry:AuthorizationRegistry,
           durable_journal: DurableExecutionJournal | None = None,operational_controls=None):
    _require_live_controls(broker,durable_journal,operational_controls)
    request = translate_order(intent, timestamp)
    if operational_controls is not None:operational_controls.validate("SUBMIT",request,timestamp)
    if any(item.request.client_order_id == request.client_order_id for item in state.orders): raise ValueError("duplicate client order ID")
    local = LocalOrder(request,None,LiveOrderStatus.NEW,request.quantity*0,(),timestamp,intent.intent)
    local = transition(local, LiveOrderStatus.SUBMITTED, timestamp)
    log = append_event(log, ExecutionEventType.SUBMITTED, request.client_order_id, timestamp)
    mutation_id = "SUBMIT:" + request.client_order_id
    if durable_journal is not None:
        durable_journal.prepare(mutation_id,"SUBMIT",request.client_order_id,
                                intent.authorization.authorization_id,request,timestamp)
    consume(authorization_registry,intent.intent,intent.authorization,timestamp)
    if durable_journal is not None:
        durable_journal.transition(mutation_id,MutationState.PREPARED,MutationState.AUTHORIZED,timestamp)
        durable_journal.transition(mutation_id,MutationState.AUTHORIZED,MutationState.DISPATCHING,timestamp)
    response = broker.submit_order(request)
    if durable_journal is not None:
        durable_journal.transition(mutation_id,MutationState.DISPATCHING,MutationState.ACKNOWLEDGED,
                                   timestamp,response.broker_order_id)
    local = _apply_broker_order(local, response, timestamp)
    log = append_event(log, _event_for_status(local.status), request.client_order_id, timestamp,
                       (("broker_order_id", response.broker_order_id),))
    return upsert_order(state, local), log


def cancel(client_order_id:str,validated_intent:ValidatedExecutionIntent,broker:Broker,
           state:LocalPortfolioState,log:ExecutionEventLog,timestamp:datetime,authorization_registry:AuthorizationRegistry,
           durable_journal:DurableExecutionJournal|None=None,operational_controls=None):
    _require_live_controls(broker,durable_journal,operational_controls)
    local = _find(state, client_order_id)
    if local.intent is None or local.intent!=validated_intent.intent:raise ValueError("cancellation authorization intent mismatch")
    if operational_controls is not None:operational_controls.validate("CANCEL",local.request,timestamp)
    mutation_id="CANCEL:"+client_order_id
    if durable_journal is not None:durable_journal.prepare(mutation_id,"CANCEL",client_order_id,validated_intent.authorization.authorization_id,None,timestamp)
    consume(authorization_registry,validated_intent.intent,validated_intent.authorization,timestamp)
    if durable_journal is not None:
        durable_journal.transition(mutation_id,MutationState.PREPARED,MutationState.AUTHORIZED,timestamp)
        durable_journal.transition(mutation_id,MutationState.AUTHORIZED,MutationState.DISPATCHING,timestamp)
    response = broker.cancel_order(client_order_id)
    if durable_journal is not None:durable_journal.transition(mutation_id,MutationState.DISPATCHING,MutationState.ACKNOWLEDGED,timestamp,response.broker_order_id)
    updated = _apply_broker_order(local, response, timestamp)
    if updated.status is not LiveOrderStatus.CANCELLED: raise ValueError("broker did not confirm cancellation")
    return upsert_order(state, updated), append_event(log, ExecutionEventType.CANCELLED, client_order_id, timestamp)


def replace_order(replacement: ReplacementRequest, broker: Broker, state: LocalPortfolioState,
                  log: ExecutionEventLog, timestamp: datetime,authorization_registry:AuthorizationRegistry,
                  durable_journal:DurableExecutionJournal|None=None,operational_controls=None):
    _require_live_controls(broker,durable_journal,operational_controls)
    local = _find(state, replacement.client_order_id)
    if local.status not in (LiveOrderStatus.SUBMITTED, LiveOrderStatus.ACKNOWLEDGED, LiveOrderStatus.PARTIALLY_FILLED): raise ValueError("order cannot be replaced in its current state")
    request = replace(local.request, quantity=replacement.quantity, limit_price=replacement.limit_price,
                      stop_price=replacement.stop_price)
    if replacement.validated_intent.intent.intent_id!=replacement.client_order_id:raise ValueError("replacement intent identity mismatch")
    expected=replacement.validated_intent.intent
    if (expected.symbol,expected.side,expected.order_type,expected.quantity,expected.limit_price,expected.stop_price,expected.time_in_force)!=(request.symbol,request.side,request.order_type,request.quantity,request.limit_price,request.stop_price,request.time_in_force):raise ValueError("replacement authorization details mismatch")
    validate_broker_order_request(request)
    if operational_controls is not None:operational_controls.validate("REPLACE",request,timestamp)
    if request.quantity < local.filled_quantity:
        raise ValueError("replacement quantity cannot be below the already filled quantity")
    mutation_id="REPLACE:"+replacement.client_order_id
    if durable_journal is not None:durable_journal.prepare(mutation_id,"REPLACE",replacement.client_order_id,replacement.validated_intent.authorization.authorization_id,request,timestamp)
    consume(authorization_registry,expected,replacement.validated_intent.authorization,timestamp)
    if durable_journal is not None:
        durable_journal.transition(mutation_id,MutationState.PREPARED,MutationState.AUTHORIZED,timestamp)
        durable_journal.transition(mutation_id,MutationState.AUTHORIZED,MutationState.DISPATCHING,timestamp)
    response = broker.replace_order(replacement.client_order_id, request)
    if durable_journal is not None:durable_journal.transition(mutation_id,MutationState.DISPATCHING,MutationState.ACKNOWLEDGED,timestamp,response.broker_order_id)
    updated = _apply_broker_order(replace(local, request=request), response, timestamp)
    return upsert_order(state, updated), append_event(log, ExecutionEventType.REPLACED,
                                                       replacement.client_order_id, timestamp)


def reconcile_fills(state: LocalPortfolioState, fills: tuple[BrokerFill, ...], log: ExecutionEventLog):
    for fill in sorted(fills, key=lambda item: (item.timestamp, item.fill_id)):
        local = next((item for item in state.orders if item.broker_order_id == fill.broker_order_id), None)
        if local is None or any(item.fill_id == fill.fill_id for item in local.fills): continue
        combined = (*local.fills, fill)
        total = sum((item.quantity for item in combined), start=fill.quantity * 0)
        status = LiveOrderStatus.FILLED if total == local.request.quantity else LiveOrderStatus.PARTIALLY_FILLED
        updated = transition(local, status, fill.timestamp, fills=combined)
        state = upsert_order(state, updated)
        event = ExecutionEventType.FILL if status is LiveOrderStatus.FILLED else ExecutionEventType.PARTIAL_FILL
        log = append_event(log, event, local.request.client_order_id, fill.timestamp,
                           (("fill_id", fill.fill_id), ("quantity", str(fill.quantity)), ("price", str(fill.price))))
    return state, log


def _apply_broker_order(local, response, timestamp):
    if response.client_order_id != local.request.client_order_id: raise ValueError("broker client order ID mismatch")
    local = replace(local, broker_order_id=response.broker_order_id)
    return transition(local, response.status, timestamp)


def _find(state, client_order_id):
    result = next((item for item in state.orders if item.request.client_order_id == client_order_id), None)
    if result is None: raise ValueError("local order was not found")
    return result


def _event_for_status(status):
    return {LiveOrderStatus.ACKNOWLEDGED: ExecutionEventType.ACKNOWLEDGED,
            LiveOrderStatus.REJECTED: ExecutionEventType.REJECTED,
            LiveOrderStatus.CANCELLED: ExecutionEventType.CANCELLED,
            LiveOrderStatus.PARTIALLY_FILLED: ExecutionEventType.PARTIAL_FILL,
            LiveOrderStatus.FILLED: ExecutionEventType.FILL}.get(status, ExecutionEventType.SUBMITTED)


def _require_live_controls(broker,durable_journal,operational_controls):
    if getattr(broker,"requires_durable_journal",False) and not isinstance(durable_journal,DurableExecutionJournal):
        raise ValueError("capability-bound live execution requires a durable execution journal")
    if getattr(broker,"requires_durable_journal",False) and operational_controls is None:
        raise ValueError("capability-bound live execution requires operational controls")

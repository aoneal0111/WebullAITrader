from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.live_execution.events import ExecutionEventLog
from app.live_execution.models import (
    BrokerAccount, BrokerCash, BrokerFill, BrokerOrder, BrokerPosition, LiveExecutionAuthorization,
    LiveOrderStatus, LiveOrderType, LiveSide, LocalOrder, LocalPortfolioState, ReplacementRequest,
    TimeInForce, ValidatedExecutionIntent,
)
from app.live_execution.order_manager import cancel, reconcile_fills, replace_order, submit
from app.live_execution.order_translation import translate_order
from app.live_execution.portfolio_state import transition
from app.live_execution.report import execution_to_json, execution_to_text
from app.live_execution.synchronization import synchronize
from app.live_execution.webull_adapter import WebullAdapter
from app.authorization import (AuthorizationRegistry,ComplianceApprovalEvidence,ExecutionIntent,
    RiskApprovalEvidence,ValidatedExecutionIntent,intent_digest,issue_authorization)

D = Decimal
NOW = datetime(2026, 7, 18, 15, tzinfo=UTC)


def execution_intent(order_type=LiveOrderType.LIMIT,quantity="2",limit="10",stop=None):
    return ExecutionIntent("req-1","account-1","XYZ",LiveSide.BUY,order_type,D(quantity),
        None if limit is None else D(limit),None if stop is None else D(stop),TimeInForce.DAY,NOW-timedelta(seconds=1))

def authorize(base=None,registry=None,auth_id="auth-1"):
    base=base or execution_intent();registry=registry or AuthorizationRegistry();digest=intent_digest(base)
    fields=(base.intent_id,digest,True,base.account_id,base.symbol,base.side,base.order_type,base.quantity,
            base.limit_price,base.stop_price,base.time_in_force,NOW-timedelta(minutes=1),NOW+timedelta(minutes=5))
    risk=RiskApprovalEvidence("risk-"+auth_id,*fields);compliance=ComplianceApprovalEvidence("compliance-"+auth_id,*fields)
    auth,registry=issue_authorization(base,risk,compliance,authorization_id=auth_id,issued_at=NOW-timedelta(seconds=1),
        expires_at=NOW+timedelta(minutes=4),single_use=True,registry=registry)
    return ValidatedExecutionIntent(base,auth),registry

def intent(order_type=LiveOrderType.LIMIT,quantity="2",limit="10",stop=None):return authorize(execution_intent(order_type,quantity,limit,stop))[0]


def broker_order(request, status=LiveOrderStatus.ACKNOWLEDGED, filled="0"):
    return BrokerOrder("broker-1", request.client_order_id, request.symbol, request.side, request.order_type,
                       request.quantity, D(filled), request.limit_price, request.stop_price,
                       request.time_in_force, status, NOW)


class MockBroker:
    def __init__(self):
        self.connected = False; self.submissions = []; self.cancellations = []; self.replacements = []
        self.orders = (); self.positions = (); self.cash = BrokerCash(D("1000"), D("0"), "USD"); self.fills = ()
    def connect(self): self.connected = True
    def disconnect(self): self.connected = False
    def submit_order(self, request): self.submissions.append(request); return broker_order(request)
    def cancel_order(self, client_order_id):
        self.cancellations.append(client_order_id); local = self.submissions[-1]
        return broker_order(local, LiveOrderStatus.CANCELLED)
    def replace_order(self, client_order_id, request): self.replacements.append(request); return broker_order(request)
    def get_positions(self): return self.positions
    def get_orders(self): return self.orders
    def get_cash(self): return self.cash
    def get_account(self): return BrokerAccount("****1234", "CASH", "ACTIVE")
    def get_fills(self): return self.fills


def submitted_state():
    broker=MockBroker();validated,registry=authorize()
    return (*submit(validated,broker,LocalPortfolioState(),ExecutionEventLog(),NOW,registry),broker,registry)


@pytest.mark.parametrize("args",((LiveOrderType.LIMIT,"0","10",None),(LiveOrderType.LIMIT,"NaN","10",None),
    (LiveOrderType.LIMIT,"2",None,None),(LiveOrderType.MARKET,"2","10",None),(LiveOrderType.STOP,"2",None,None)))
def test_order_validation_rejects_malformed_intents(args):
    with pytest.raises(ValueError): intent(*args)


def test_translation_is_exact_and_requires_live_authorization():
    request = translate_order(intent(), NOW)
    assert (request.symbol, request.quantity, request.limit_price) == ("XYZ", D("2"), D("10"))
    expired = replace(intent(), authorization=replace(intent().authorization, expires_at=NOW - timedelta(seconds=1)))
    with pytest.raises(ValueError, match="authorization"): translate_order(expired, NOW)


def test_submit_and_state_machine_transition():
    state, log, broker,_ = submitted_state()
    assert state.orders[0].status is LiveOrderStatus.ACKNOWLEDGED
    assert len(broker.submissions) == 1
    assert tuple(item.event_type.value for item in log.events) == ("SUBMITTED", "ACKNOWLEDGED")
    with pytest.raises(ValueError, match="illegal"): transition(state.orders[0], LiveOrderStatus.NEW, NOW)


def test_partial_and_multiple_fill_reconciliation_is_idempotent():
    state, log, _,_ = submitted_state()
    fills = (BrokerFill("fill-1", "broker-1", D("0.5"), D("10"), NOW + timedelta(seconds=1)),
             BrokerFill("fill-2", "broker-1", D("1.5"), D("10"), NOW + timedelta(seconds=2)))
    partial, partial_log = reconcile_fills(state, fills[:1], log)
    assert partial.orders[0].status is LiveOrderStatus.PARTIALLY_FILLED
    completed, completed_log = reconcile_fills(partial, fills, partial_log)
    assert completed.orders[0].status is LiveOrderStatus.FILLED
    assert completed.orders[0].filled_quantity == D("2")
    repeated, repeated_log = reconcile_fills(completed, fills, completed_log)
    assert (repeated, repeated_log) == (completed, completed_log)


def test_fill_above_quantity_is_rejected():
    state, log, _,_ = submitted_state()
    with pytest.raises(ValueError, match="exceeds"):
        reconcile_fills(state, (BrokerFill("bad", "broker-1", D("3"), D("10"), NOW),), log)


def test_cancellation_requires_authorization_and_confirmation():
    state,log,broker,registry=submitted_state();validated,registry=authorize(state.orders[0].intent,registry,"auth-cancel")
    state,log=cancel("req-1",validated,broker,state,log,NOW,registry)
    assert state.orders[0].status is LiveOrderStatus.CANCELLED
    assert broker.cancellations == ["req-1"]
    with pytest.raises(ValueError): cancel("req-1",validated,broker,state,log,NOW,registry)


def test_replacement_is_explicit_and_does_not_mutate_prior_state():
    state,log,broker,registry=submitted_state();replacement_intent=replace(state.orders[0].intent,quantity=D("3"),limit_price=D("11"))
    validated,registry=authorize(replacement_intent,registry,"auth-replace")
    replacement=ReplacementRequest("req-1",D("3"),D("11"),None,validated)
    updated,updated_log=replace_order(replacement,broker,state,log,NOW,registry)
    assert state.orders[0].request.quantity == D("2")
    assert updated.orders[0].request.quantity == D("3")
    assert updated_log.events[-1].event_type.value == "REPLACED"


def test_synchronization_reports_differences_without_overwriting():
    state, log, broker,_ = submitted_state()
    request = state.orders[0].request
    broker.orders = (broker_order(request, LiveOrderStatus.PARTIALLY_FILLED, "1"),)
    broker.positions = (BrokerPosition("XYZ", D("1"), D("10"), D("10")),)
    report, updated_log = synchronize(broker, state, log, NOW)
    assert report.differences
    assert report.reconciled_state is state
    repeated, repeated_log = synchronize(broker, state, updated_log, NOW + timedelta(seconds=1))
    assert repeated.differences == report.differences
    assert repeated_log == updated_log


def test_webull_adapter_is_transport_isolated_and_connection_guarded():
    broker = MockBroker(); adapter = WebullAdapter(broker)
    with pytest.raises(RuntimeError): adapter.get_cash()
    adapter.connect(); assert adapter.get_cash() == broker.cash
    adapter.disconnect(); assert not broker.connected


def test_canonical_json_and_text_are_stable():
    state, log, _,_ = submitted_state()
    assert execution_to_json((state, log)) == execution_to_json((state, log))
    assert execution_to_text((state, log)) == execution_to_text((state, log))
    assert '"quantity":"2"' in execution_to_json(state)


def test_no_upstream_or_network_imports():
    package = Path(__file__).parents[1] / "app" / "live_execution"
    content = "\n".join(path.read_text(encoding="utf-8").lower() for path in package.glob("*.py"))
    forbidden = ("app.ai", "app.indicators", "app.strategy", "app.analytics", "app.monte_carlo",
                 "app.stress_testing", "app.risk", "app.compliance", "requests", "httpx", "mcp")
    assert not any(f"from {item}" in content or f"import {item}" in content for item in forbidden)

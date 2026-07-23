from datetime import timedelta
from decimal import Decimal
import pytest
from app.broker_execution import *
from tests.broker_execution.helpers import request,policy,snapshot,human
def auth(**x):return ExecutionSafetyGate().authorize(request(**x))
def test_paper_approval_determinism_checks_and_decimal_math():
    r=request();a=ExecutionSafetyGate().authorize(r);assert a.decision is SafetyDecision.APPROVED and a.reason is SafetyReason.APPROVED
    assert a==ExecutionSafetyGate().authorize(r);assert isinstance(a.order_notional,Decimal);assert a.order_notional==a.entry_price*a.quantity
    assert [x.name for x in a.checks]==list(("request valid","proposal ready","symbol valid","quantity positive","entry price positive","kill switch inactive","mode permitted","human authorization valid","duplicate request absent","quantity within limit","order notional within limit","projected position within limit","daily loss limit not reached"))
@pytest.mark.parametrize("changes,reason",[
 ({"policy":policy(kill_switch_active=True)},SafetyReason.KILL_SWITCH_ACTIVE),
 ({"policy":policy(allowed_symbols=("MSFT",))},SafetyReason.INVALID_SYMBOL),
 ({"mode":ExecutionMode.LIVE},SafetyReason.LIVE_MODE_DISABLED),
 ({"account_snapshot":snapshot(recent_authorization_fingerprints=("fp-1",))},SafetyReason.DUPLICATE_REQUEST),
 ({"policy":policy(maximum_order_quantity=1)},SafetyReason.QUANTITY_EXCEEDS_LIMIT),
 ({"policy":policy(maximum_order_notional=1)},SafetyReason.NOTIONAL_EXCEEDS_LIMIT),
 ({"policy":policy(maximum_symbol_position=1)},SafetyReason.POSITION_EXCEEDS_LIMIT),
 ({"account_snapshot":snapshot(current_daily_realized_pnl=-1000)},SafetyReason.DAILY_LOSS_LIMIT_REACHED),
 ({"policy":policy(maximum_daily_loss=0)},SafetyReason.DAILY_LOSS_LIMIT_REACHED)])
def test_rejections(changes,reason):assert auth(**changes).reason is reason
def test_live_human_rules():
    r=request(mode=ExecutionMode.LIVE,policy=policy(live_mode_enabled=True,require_human_authorization=True));assert ExecutionSafetyGate().authorize(r).reason is SafetyReason.HUMAN_AUTHORIZATION_REQUIRED
    assert ExecutionSafetyGate().authorize(request(mode=ExecutionMode.LIVE,policy=r.policy,human_authorization=human(r.proposal))).decision is SafetyDecision.APPROVED
    assert ExecutionSafetyGate().authorize(request(mode=ExecutionMode.LIVE,policy=r.policy,human_authorization=human(r.proposal,approved=False))).reason is SafetyReason.HUMAN_AUTHORIZATION_INVALID
    expired=human(r.proposal,timestamp=r.timestamp-timedelta(minutes=2),expires_at=r.timestamp-timedelta(seconds=1))
    assert ExecutionSafetyGate().authorize(request(mode=ExecutionMode.LIVE,policy=r.policy,human_authorization=expired)).reason is SafetyReason.HUMAN_AUTHORIZATION_INVALID

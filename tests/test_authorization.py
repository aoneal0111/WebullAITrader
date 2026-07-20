from __future__ import annotations
from dataclasses import replace
from datetime import UTC,datetime,timedelta
from decimal import Decimal
from pathlib import Path
import pytest

from app.authorization import (AuthorizationRegistry,ComplianceApprovalEvidence,ExecutionIntent,
    RiskApprovalEvidence,ValidatedExecutionIntent,authorization_from_json,authorization_to_json,consume,intent_digest,
    issue_authorization,revoke,validate_known)
from app.broker_protocol.models import BrokerOrderType,BrokerSide,TimeInForce
from app.live_execution.models import BrokerOrderRequest as CompatibilityRequest,LiveSide
from app.live_execution.order_manager import submit
from app.live_execution.events import ExecutionEventLog
from app.live_execution.models import LocalPortfolioState

D=Decimal;NOW=datetime(2026,7,18,tzinfo=UTC)
def base_intent():return ExecutionIntent("intent-1","account-1","XYZ",BrokerSide.BUY,BrokerOrderType.LIMIT,D("2"),D("10"),None,TimeInForce.DAY,NOW-timedelta(minutes=2))
def evidence(intent=None,**changes):
    intent=intent or base_intent();digest=intent_digest(intent)
    values=dict(intent_id=intent.intent_id,intent_digest=digest,approved=True,account_id=intent.account_id,
        symbol=intent.symbol,side=intent.side,order_type=intent.order_type,quantity=intent.quantity,
        limit_price=intent.limit_price,stop_price=intent.stop_price,time_in_force=intent.time_in_force,
        issued_at=NOW-timedelta(minutes=1),expires_at=NOW+timedelta(minutes=10),revoked=False,superseded=False)
    values.update(changes)
    return RiskApprovalEvidence("risk-1",**values),ComplianceApprovalEvidence("compliance-1",**values)
def issue(intent=None,risk=None,compliance=None,registry=None,auth_id="auth-1",expires=None):
    intent=intent or base_intent();default_risk,default_compliance=evidence(intent)
    return issue_authorization(intent,risk or default_risk,compliance or default_compliance,
        authorization_id=auth_id,issued_at=NOW,expires_at=expires or NOW+timedelta(minutes=5),single_use=True,
        registry=registry or AuthorizationRegistry())

def test_matching_evidence_issues_bound_deterministic_authorization():
    intent=base_intent();authorization,registry=issue(intent)
    assert authorization.intent_digest==intent_digest(intent)
    assert ValidatedExecutionIntent(intent,authorization).authorization is authorization
    assert authorization_to_json(authorization)==authorization_to_json(issue(intent)[0])
    assert authorization_from_json(authorization_to_json(authorization))==authorization
    assert len(authorization.intent_digest)==64

def test_legacy_authorization_schema_fails_closed():
    with pytest.raises(ValueError,match="legacy authorization"):
        authorization_from_json('{"authorization_id":"old"}')

@pytest.mark.parametrize(("field","value"),(("intent_id","other"),("account_id","other"),("symbol","ABC"),
    ("side",BrokerSide.SELL),("quantity",D("3")),("order_type",BrokerOrderType.STOP),
    ("limit_price",D("11")),("stop_price",D("9"))))
def test_mismatched_risk_or_compliance_evidence_is_rejected(field,value):
    intent=base_intent();risk,compliance=evidence(intent);risk=replace(risk,**{field:value})
    with pytest.raises(ValueError,match="mismatch"):issue(intent,risk,compliance)

def test_mismatched_digest_and_compliance_intent_are_rejected():
    intent=base_intent();risk,compliance=evidence(intent)
    with pytest.raises(ValueError):issue(intent,replace(risk,intent_digest="0"*64),compliance)
    with pytest.raises(ValueError):issue(intent,risk,replace(compliance,intent_id="other"))

@pytest.mark.parametrize("which",("risk","compliance"))
def test_expired_approval_is_rejected(which):
    intent=base_intent();risk,compliance=evidence(intent);expired=NOW-timedelta(seconds=1)
    risk=replace(risk,expires_at=expired) if which=="risk" else risk
    compliance=replace(compliance,expires_at=expired) if which=="compliance" else compliance
    with pytest.raises(ValueError,match="valid"):issue(intent,risk,compliance)

def test_authorization_cannot_outlive_evidence():
    intent=base_intent();risk,compliance=evidence(intent,expires_at=NOW+timedelta(minutes=2))
    with pytest.raises(ValueError,match="outlive"):issue(intent,risk,compliance,expires=NOW+timedelta(minutes=3))

@pytest.mark.parametrize("field",("revoked","superseded"))
def test_revoked_or_superseded_evidence_is_rejected(field):
    intent=base_intent();risk,compliance=evidence(intent);risk=replace(risk,**{field:True})
    with pytest.raises(ValueError,match="revoked or superseded"):issue(intent,risk,compliance)

def test_naive_timestamps_and_nonfinite_decimals_are_rejected():
    with pytest.raises(ValueError,match="timezone"):intent_digest(replace(base_intent(),created_at=datetime(2026,1,1)))
    with pytest.raises(ValueError,match="finite"):intent_digest(replace(base_intent(),quantity=D("NaN")))

def test_authorization_substitution_unknown_revoked_and_single_use_fail_closed():
    intent=base_intent();authorization,registry=issue(intent);validated=ValidatedExecutionIntent(intent,authorization)
    consume(registry,intent,authorization,NOW)
    with pytest.raises(ValueError,match="consumed"):consume(registry,intent,authorization,NOW)
    other,_=issue(intent,auth_id="other")
    with pytest.raises(ValueError,match="unknown"):validate_known(registry,intent,other,NOW)
    authorization2,registry=issue(intent,registry=registry,auth_id="auth-2");revoke(registry,"auth-2")
    with pytest.raises(ValueError,match="revoked"):validate_known(registry,intent,authorization2,NOW)
    with pytest.raises(ValueError):ValidatedExecutionIntent(replace(intent,quantity=D("3")),authorization)

def test_compatibility_aliases_are_identical_class_objects():
    from app.broker_protocol.models import BrokerOrderRequest,BrokerSide
    from app.live_execution.models import BrokerOrderRequest as OldRequest,LiveSide as OldSide
    assert OldRequest is BrokerOrderRequest and OldSide is BrokerSide and CompatibilityRequest is BrokerOrderRequest and LiveSide is BrokerSide

def test_failed_dispatch_still_consumes_single_use_authorization():
    intent=base_intent();authorization,registry=issue(intent);validated=ValidatedExecutionIntent(intent,authorization)
    class FailedBroker:
        def submit_order(self,order):raise RuntimeError("ambiguous transport failure")
    with pytest.raises(RuntimeError):submit(validated,FailedBroker(),LocalPortfolioState(),ExecutionEventLog(),NOW,registry)
    assert authorization.authorization_id in registry.consumed_ids
    with pytest.raises(ValueError,match="consumed"):submit(validated,FailedBroker(),LocalPortfolioState(),ExecutionEventLog(),NOW,registry)

def test_import_boundaries():
    root=Path(__file__).parents[1]/"app"
    webull="\n".join(p.read_text(encoding="utf-8") for p in (root/"webull").glob("*.py"))
    assert "app.live_execution" not in webull and "app.authorization" not in webull
    broker="\n".join(p.read_text(encoding="utf-8") for p in (root/"broker_protocol").glob("*.py"))
    assert not any(name in broker for name in ("app.live_execution","app.webull","app.authorization","app.risk","app.compliance","app.market_data"))

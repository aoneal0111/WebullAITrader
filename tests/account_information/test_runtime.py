import pytest
from app.account_information import *
from app.session import SessionSnapshot, SessionStatus
from tests.account_information.fixtures import enabled_policy, request
from tests.account_information.helpers import FakeGateway, FakeSessionManager, active_snapshot


def runtime(session=None,gateway=None,policy=None): return DeterministicAccountInformationRuntime(session or FakeSessionManager(),gateway or FakeGateway(),policy or enabled_policy())


def test_construction_performs_no_work():
    s,g=FakeSessionManager(),FakeGateway(); runtime(s,g); assert s.calls==0 and g.requests==[]


def test_success_resolves_and_invokes_exactly_once():
    s,g=FakeSessionManager(),FakeGateway(); result=runtime(s,g).get_account_information(request())
    assert result.decision is AccountInformationDecision.SUCCESS and result.account_id=="account-1"
    assert s.calls==1 and g.requests==[request()]


def test_disabled_does_no_dependency_work():
    s,g=FakeSessionManager(),FakeGateway(); result=runtime(s,g,AccountInformationPolicy()).get_account_information(request())
    assert result.decision is AccountInformationDecision.DISABLED and s.calls==0 and not g.requests


@pytest.mark.parametrize("snapshot",[SessionSnapshot(SessionStatus.NO_SESSION,None,(),0),active_snapshot("other")])
def test_invalid_session_returns_failure_without_gateway(snapshot):
    s,g=FakeSessionManager(snapshot),FakeGateway(); result=runtime(s,g).get_account_information(request())
    assert result.decision is AccountInformationDecision.SESSION_INVALID and s.calls==1 and not g.requests


def test_gateway_failure_is_normalized_without_retry():
    g=FakeGateway(error=OSError("synthetic")); result=runtime(gateway=g).get_account_information(request())
    assert result.decision is AccountInformationDecision.GATEWAY_FAILURE and len(g.requests)==1


def test_bad_dependency_outputs_raise_domain_error():
    with pytest.raises(AccountInformationDependencyError): runtime(session=FakeSessionManager(snapshot="bad")).get_account_information(request())
    with pytest.raises(AccountInformationDependencyError): runtime(gateway=FakeGateway(response="bad")).get_account_information(request())


def test_session_dependency_error_preserves_cause():
    with pytest.raises(AccountInformationDependencyError) as caught: runtime(session=FakeSessionManager(error=LookupError("synthetic"))).get_account_information(request())
    assert isinstance(caught.value.__cause__,LookupError)


def test_identical_inputs_produce_identical_results_and_are_not_mutated():
    r=request(); before=r.to_dict(); assert runtime().get_account_information(r)==runtime().get_account_information(r); assert r.to_dict()==before

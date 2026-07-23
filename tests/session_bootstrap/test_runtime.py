from app.session_bootstrap import *
from tests.session_bootstrap.fixtures import approved_profile,request
from tests.session_bootstrap.helpers import FakeAuthenticationRuntime,FakeProvider,FakeSessionManager
def build(approval=None,provider=None,authentication=None,manager=None,policy=None):
 values=(provider or FakeProvider(),authentication or FakeAuthenticationRuntime(),manager or FakeSessionManager());return DeterministicSessionBootstrapRuntime(approval or approved_profile(),*values,policy or SessionBootstrapPolicy(enabled=True)),values
def test_construction_and_disabled_perform_no_work():
 runtime,(p,a,s)=build(policy=SessionBootstrapPolicy());result=runtime.bootstrap(request());assert result.decision is SessionBootstrapDecision.DISABLED;assert p.calls==a.calls==s.calls==[]
def test_success_exactly_one_each_and_preserves_ids():
 runtime,(p,a,s)=build();source=request();result=runtime.bootstrap(source);assert result.success;assert len(p.calls)==len(a.calls)==len(s.calls)==1;assert result.bootstrap_id=="bootstrap-1";assert result.approved_profile_id==approved_profile().profile_id;assert result.authentication_result_id=="response-1";assert result.session_id=="session-1";assert result.session_handle.status.value=="CREATED";assert [x.name for x in result.criteria_results]==["policy_enabled","profile_approved","authentication_succeeded","session_created"]
def test_authentication_failure_does_not_call_session_and_no_retry():
 runtime,(p,a,s)=build(authentication=FakeAuthenticationRuntime(False));result=runtime.bootstrap(request());assert result.decision is SessionBootstrapDecision.AUTHENTICATION_FAILED;assert len(p.calls)==len(a.calls)==1;assert s.calls==[]
def test_rejected_approval_returns_failure_without_dependency_calls():
 from app.webull_authentication_approval import DeterministicWebullAuthenticationProfileApprovalService,WebullAuthenticationProfileApprovalPolicy
 from tests.webull_authentication_approval.fixtures import request as approval_request
 rejected=DeterministicWebullAuthenticationProfileApprovalService(WebullAuthenticationProfileApprovalPolicy()).approve(approval_request());runtime,(p,a,s)=build(approval=rejected);result=runtime.bootstrap(request());assert result.decision is SessionBootstrapDecision.APPROVAL_REJECTED;assert result.session_handle is None;assert p.calls==a.calls==s.calls==[]
def test_session_failure_returns_result_and_calls_once():
 runtime,(p,a,s)=build(manager=FakeSessionManager(error=LookupError()));result=runtime.bootstrap(request());assert result.decision is SessionBootstrapDecision.SESSION_CREATION_FAILED;assert len(p.calls)==len(a.calls)==len(s.calls)==1
def test_equivalent_inputs_deterministic_no_mutation():
 one,_=build();two,_=build();source=request();before=source.to_dict();assert one.bootstrap(source)==two.bootstrap(request());assert source.to_dict()==before

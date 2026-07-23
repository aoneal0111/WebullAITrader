from dataclasses import FrozenInstanceError
import pytest
from app.session_bootstrap import *
from tests.session_bootstrap.fixtures import request
from tests.session_bootstrap.helpers import FakeSessionManager
def test_request_frozen_slotted_roundtrip_no_credentials_serialized():
 r=request();assert SessionBootstrapRequest.from_dict(r.to_dict())==r;assert not hasattr(r,"__dict__")
 with pytest.raises(FrozenInstanceError):r.bootstrap_id="x"
 with pytest.raises(TypeError):r.metadata["x"]=1
 rendered=repr(r.to_dict());assert "opaque-secret" not in rendered
def test_criteria_and_success_result_roundtrip():
 r=request();handle=FakeSessionManager().create(r.session_request);result=SessionBootstrapResult(r.bootstrap_id,"profile",r.authentication_attempt_id,r.session_request.identifier.value,True,SessionBootstrapDecision.SUCCESS,handle,(SessionBootstrapCriteriaResult("created",True,"session created"),));assert SessionBootstrapResult.from_dict(result.to_dict())==result
def test_failure_result_cannot_expose_session():
 r=request()
 with pytest.raises(SessionBootstrapValidationError):SessionBootstrapResult(r.bootstrap_id,"profile","auth","session",False,SessionBootstrapDecision.AUTHENTICATION_FAILED,FakeSessionManager().create(r.session_request),(),{})

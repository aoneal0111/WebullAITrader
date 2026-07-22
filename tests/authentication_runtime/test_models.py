from dataclasses import FrozenInstanceError
import pytest
from app.authentication_runtime import AuthenticationRuntimeContext,AuthenticationRuntimeRequest,AuthenticationRuntimeResult
from tests.authentication_runtime.helpers import request,transport_result
def test_models_frozen_slotted_roundtrip_and_context():
 r=request();result=AuthenticationRuntimeResult(r.attempt_id,True,transport_result(),r.context,"authentication_runtime_policy_v1")
 assert AuthenticationRuntimeRequest.from_dict(r.to_dict())==r;assert AuthenticationRuntimeResult.from_dict(result.to_dict())==result;assert not hasattr(r,"__dict__");assert result.context is r.context
 with pytest.raises(FrozenInstanceError):r.attempt_id="x"
 with pytest.raises(TypeError):r.context.metadata["x"]=1
def test_result_consistency_validation():
 r=request()
 with pytest.raises(ValueError):AuthenticationRuntimeResult(r.attempt_id,False,transport_result(True),r.context,"policy")

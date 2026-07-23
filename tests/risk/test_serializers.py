import pytest
from app.risk import *
from tests.risk.fixtures import context,enabled_policy
def test_runtime_serializers():
 value=context();result=DeterministicRiskEvaluator().evaluate(value,enabled_policy());assert serialize_context(value)==value.to_dict() and serialize_result(result)==result.to_dict()
def test_runtime_serializer_type():
 with pytest.raises(RiskRuntimeSerializationError):serialize_context(object())

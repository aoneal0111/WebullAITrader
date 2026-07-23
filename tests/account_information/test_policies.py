from dataclasses import FrozenInstanceError
import pytest
from app.account_information import *


def test_policy_safe_default_frozen_and_roundtrip():
    p=AccountInformationPolicy(); assert not p.enabled and p.strict_validation and not hasattr(p,"__dict__")
    assert AccountInformationPolicy.from_dict(p.to_dict())==p
    with pytest.raises(FrozenInstanceError): p.enabled=True
    with pytest.raises(TypeError): p.metadata["x"]=1


@pytest.mark.parametrize("kwargs",[{"enabled":1},{"strict_validation":0},{"version":""}])
def test_policy_validation(kwargs):
    with pytest.raises(AccountInformationValidationError): AccountInformationPolicy(**kwargs)

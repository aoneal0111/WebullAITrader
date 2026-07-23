from dataclasses import FrozenInstanceError
import pytest
from app.webull_authentication_approval import WebullAuthenticationProfileApprovalPolicy
def test_policy_disabled_frozen_roundtrip():
 p=WebullAuthenticationProfileApprovalPolicy();assert not p.enabled;assert WebullAuthenticationProfileApprovalPolicy.from_dict(p.to_dict())==p
 with pytest.raises(FrozenInstanceError):p.enabled=True
@pytest.mark.parametrize("name",["enabled","require_all_material_fields_bound","require_all_assessments_eligible","reject_contradicted_assessments","reject_disabled_assessments","reject_missing_assessments","allow_synthetic_evidence","strict_validation"])
def test_strict_boolean_validation(name):
 with pytest.raises(ValueError):WebullAuthenticationProfileApprovalPolicy(**{name:1})

from dataclasses import FrozenInstanceError
import pytest
from app.webull_protocol_evidence import WebullProtocolEvidencePolicy
def test_policy_safe_defaults_frozen_roundtrip():
 p=WebullProtocolEvidencePolicy();assert not p.enabled and not p.allow_synthetic_support;assert WebullProtocolEvidencePolicy.from_dict(p.to_dict())==p
 with pytest.raises(FrozenInstanceError):p.enabled=True
@pytest.mark.parametrize("kwargs",[{"version":""},{"enabled":1},{"minimum_supporting_records":-1},{"minimum_independent_groups":True},{"allow_synthetic_support":1}])
def test_policy_validation(kwargs):
 with pytest.raises(ValueError):WebullProtocolEvidencePolicy(**kwargs)

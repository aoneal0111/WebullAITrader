from dataclasses import FrozenInstanceError
import json,pytest
from app.http_runtime import *
def test_default_disabled_roundtrip():
 p=HTTPRuntimePolicy();assert not p.runtime_enabled and HTTPRuntimePolicy.from_dict(p.to_dict())==p;json.dumps(p.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):p.runtime_enabled=True
def test_booleans_strict():
 with pytest.raises(ValueError):HTTPRuntimePolicy(runtime_enabled=1)

from dataclasses import FrozenInstanceError
import json,pytest
from app.http_client import *
def test_default_disabled_roundtrip():
 p=HTTPClientPolicy();assert not p.client_enabled and HTTPClientPolicy.from_dict(p.to_dict())==p;json.dumps(p.to_dict(),allow_nan=False)
 with pytest.raises(FrozenInstanceError):p.client_enabled=True
def test_strict_bool():
 with pytest.raises(ValueError):HTTPClientPolicy(client_enabled=1)

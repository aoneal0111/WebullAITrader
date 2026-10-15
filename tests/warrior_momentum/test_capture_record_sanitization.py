from collections import UserDict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from types import MappingProxyType

import pytest

from app.strategies.warrior_momentum.forward_models import (
    CaptureRecord, CaptureRecordType,
)


AT = datetime(2026, 10, 15, 15, tzinfo=timezone.utc)


def record(payload):
    return CaptureRecord.create(CaptureRecordType.DECISION, " xyz ", AT, payload)


@pytest.mark.parametrize("mapping", [dict, UserDict, MappingProxyType])
def test_capture_sanitization_preserves_serialized_evidence_and_identity(mapping):
    value = record(mapping({"nested": [mapping({"price": Decimal("1.2300")})],
                            "at": AT, "flags": {"b", "a"}}))
    expected = ('{"at":"2026-10-15T15:00:00+00:00","flags":["a","b"],'
                '"nested":[{"price":"1.2300"}]}')
    assert value.payload_json == expected
    identity = f"1|{CaptureRecordType.DECISION.value}|XYZ|{AT.isoformat()}|{expected}"
    assert value.record_id == sha256(identity.encode()).hexdigest()


@pytest.mark.parametrize("name", [
    "SECRET", "access_token", "credential", "password", "account_id",
    "session_id", "access_key", "app_key", "api_key", "x" * 100 + "password",
])
def test_cached_schema_names_never_skip_new_nested_sensitive_fields(name):
    # Warm/evict ordinary schema keys, then reuse the same outer keys with a
    # newly sensitive nested value. Every payload still needs a full traversal.
    for i in range(300):
        record({"nested": [{"price": "1", f"field_{i}": True}]})
    with pytest.raises(ValueError, match="sensitive field is forbidden"):
        record({"nested": [UserDict({name: "must-not-be-stored"})]})


def test_cached_schema_names_never_skip_invalid_new_values():
    record({"nested": [{"price": "1"}]})
    with pytest.raises(TypeError, match="unsupported capture value"):
        record({"nested": [{"price": object()}]})

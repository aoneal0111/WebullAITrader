from __future__ import annotations

import json

import pytest
from webull.core.http.response import Response
from webull.core.utils import common

from app.webull_auth_diagnostic.diagnostic import (
    BodyIdentityError,
    BodyMode,
    TimestampMode,
    run_config_diagnostic,
)


def test_explicit_empty_body_is_identical_for_signature_and_dispatch(monkeypatch) -> None:
    signed_values = []
    dispatched_values = []
    original_sha256 = common.sha256_hex

    def capture_sha256(value):
        signed_values.append(value)
        return original_sha256(value)

    def fake_response(self):
        dispatched_values.append(self.get_body())
        return (
            401,
            {"X-Request-Id": "request-123"},
            b'{"error_code":"SIGNATURE_INVALID"}',
            object(),
        )

    monkeypatch.setattr(common, "sha256_hex", capture_sha256)
    monkeypatch.setattr(Response, "get_response_object", fake_response)

    result = run_config_diagnostic(
        app_key="diagnostic-key",
        app_secret="diagnostic-secret",
        endpoint="https://api.webull.example",
        body_mode=BodyMode.EXPLICIT_EMPTY,
    )

    assert signed_values == [""]
    assert dispatched_values == [""]
    assert signed_values[0] is dispatched_values[0]
    assert result.body_type == "str"
    assert result.body_length == 0
    assert result.http_status == 401


def test_sdk_default_preserves_none_body(monkeypatch) -> None:
    dispatched_values = []

    def fake_response(self):
        dispatched_values.append(self.get_body())
        return 200, {"X-Request-Id": "request-456"}, b"{}", object()

    monkeypatch.setattr(Response, "get_response_object", fake_response)

    result = run_config_diagnostic(
        app_key="diagnostic-key",
        app_secret="diagnostic-secret",
        endpoint="https://api.webull.example",
        body_mode=BodyMode.SDK_DEFAULT,
    )

    assert dispatched_values == [None]
    assert result.body_type == "NoneType"
    assert result.body_length == 0


def test_result_redacts_unsafe_error_code_and_request_id(monkeypatch) -> None:
    def fake_response(self):
        return (
            401,
            {"X-Request-Id": "secret/signature=="},
            b'{"error_code":"credential/leak=="}',
            object(),
        )

    monkeypatch.setattr(Response, "get_response_object", fake_response)

    result = run_config_diagnostic(
        app_key="diagnostic-key",
        app_secret="diagnostic-secret",
        endpoint="https://api.webull.example",
    )
    payload = json.loads(result.to_json())

    assert payload["sanitized_error_code"] == "REDACTED"
    assert payload["request_id"] == "REDACTED"
    serialized = result.to_json()
    assert "credential/leak" not in serialized
    assert "secret/signature" not in serialized
    assert "diagnostic-key" not in serialized
    assert "diagnostic-secret" not in serialized
    assert set(payload) == {
        "python_version",
        "sdk_version",
        "timestamp_format_classification",
        "body_type",
        "body_length",
        "http_status",
        "sanitized_error_code",
        "request_id",
    }


@pytest.mark.parametrize(
    ("timestamp_mode", "classification"),
    [
        (TimestampMode.ISO_8601_UTC, "ISO_8601_UTC_SECONDS"),
        (TimestampMode.EPOCH_MILLISECONDS, "EPOCH_MILLISECONDS"),
    ],
)
def test_timestamp_modes_are_classified(monkeypatch, timestamp_mode, classification) -> None:
    monkeypatch.setattr(
        Response,
        "get_response_object",
        lambda self: (200, {}, b"{}", object()),
    )

    result = run_config_diagnostic(
        app_key="diagnostic-key",
        app_secret="diagnostic-secret",
        endpoint="https://api.webull.example",
        timestamp_mode=timestamp_mode,
    )

    assert result.timestamp_format_classification == classification


def test_sdk_monkey_patches_restore_after_failure(monkeypatch) -> None:
    original_json_dumps = common.json_dumps_compact
    original_timestamp = common.get_iso_8601_date
    monkeypatch.setattr(
        Response,
        "get_response_object",
        lambda self: (_ for _ in ()).throw(OSError("network failed")),
    )

    result = run_config_diagnostic(
        app_key="diagnostic-key",
        app_secret="diagnostic-secret",
        endpoint="https://api.webull.example",
        body_mode=BodyMode.EXPLICIT_EMPTY,
        timestamp_mode=TimestampMode.EPOCH_MILLISECONDS,
    )

    assert result.sanitized_error_code == "DIAGNOSTIC_TRANSPORT_ERROR"
    assert common.json_dumps_compact is original_json_dumps
    assert common.get_iso_8601_date is original_timestamp


def test_body_identity_failure_is_not_hidden(monkeypatch) -> None:
    original_json_dumps = common.json_dumps_compact

    def nonidentical_empty(value):
        if value == "":
            return "".join(("",))
        return original_json_dumps(value)

    monkeypatch.setattr(common, "json_dumps_compact", nonidentical_empty)
    monkeypatch.setattr(
        Response,
        "get_response_object",
        lambda self: (200, {}, b"{}", object()),
    )

    # The diagnostic replaces the ambient serializer with its identity guard,
    # so a hostile pre-existing serializer cannot split signing and dispatch.
    result = run_config_diagnostic(
        app_key="diagnostic-key",
        app_secret="diagnostic-secret",
        endpoint="https://api.webull.example",
        body_mode=BodyMode.EXPLICIT_EMPTY,
    )
    assert result.body_type == "str"

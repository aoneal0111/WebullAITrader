"""Diagnostic-only signed GET /openapi/config comparisons.

This module deliberately uses ``ApiClient`` directly.  It never constructs a
TradeClient, initializes a token, or exposes an order operation.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import Iterator
from urllib.parse import urlparse

from webull.core.client import ApiClient
from webull.core.http.initializer.config.bean.query_config_request import (
    GetConfigRequest,
)
from webull.core.utils import common


class BodyMode(str, Enum):
    SDK_DEFAULT = "sdk-default"
    EXPLICIT_EMPTY = "explicit-empty"


class TimestampMode(str, Enum):
    ISO_8601_UTC = "iso-8601-utc"
    # Diagnostic experiment only. Webull's current documentation specifies
    # ISO 8601 UTC, so Atlas production must not use this mode.
    EPOCH_MILLISECONDS = "epoch-milliseconds"


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    python_version: str
    sdk_version: str
    timestamp_format_classification: str
    body_type: str
    body_length: int
    http_status: int | None
    sanitized_error_code: str
    request_id: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=False)


class BodyIdentityError(RuntimeError):
    """The signed body and dispatched body were not the identical value."""


_ISO_8601_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_EPOCH_MILLISECONDS = re.compile(r"^\d{13}$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class _EmptyBodySerializer:
    """Return one literal empty-string object for signing and transmission."""

    def __init__(self, original) -> None:
        self._original = original
        self.body = ""
        self.matching_calls = 0

    def __call__(self, value):
        if isinstance(value, str) and value == "":
            self.matching_calls += 1
            return self.body
        return self._original(value)


@contextmanager
def _sdk_patches(
    body_mode: BodyMode,
    timestamp_mode: TimestampMode,
) -> Iterator[_EmptyBodySerializer | None]:
    """Install request-local SDK patches and unconditionally restore them."""

    original_json_dumps = common.json_dumps_compact
    original_timestamp = common.get_iso_8601_date
    body_serializer = None
    try:
        if body_mode is BodyMode.EXPLICIT_EMPTY:
            body_serializer = _EmptyBodySerializer(original_json_dumps)
            common.json_dumps_compact = body_serializer
        if timestamp_mode is TimestampMode.EPOCH_MILLISECONDS:
            common.get_iso_8601_date = lambda dt_as_utc=None: str(
                time.time_ns() // 1_000_000
            )
        yield body_serializer
    finally:
        common.json_dumps_compact = original_json_dumps
        common.get_iso_8601_date = original_timestamp


def build_diagnostic_api_client(
    *, app_key: str, app_secret: str, endpoint: str
) -> ApiClient:
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("a secure Webull endpoint is required")
    if not app_key.strip() or not app_secret.strip():
        raise ValueError("Webull diagnostic credentials are required")
    client = ApiClient(
        app_key=app_key,
        app_secret=app_secret,
        region_id="us",
        port=parsed.port or 443,
        connect_timeout=5,
        timeout=10,
        auto_retry=False,
    )
    client.add_endpoint("us", parsed.hostname)
    return client


def run_config_diagnostic(
    *,
    app_key: str,
    app_secret: str,
    endpoint: str,
    body_mode: BodyMode = BodyMode.SDK_DEFAULT,
    timestamp_mode: TimestampMode = TimestampMode.ISO_8601_UTC,
) -> DiagnosticResult:
    """Sign and send only GET /openapi/config and return safe metadata."""

    client = build_diagnostic_api_client(
        app_key=app_key, app_secret=app_secret, endpoint=endpoint
    )
    request = GetConfigRequest()
    if body_mode is BodyMode.EXPLICIT_EMPTY:
        request.set_body_params("")

    status = None
    response_headers = {}
    response_body = b""
    safe_error = ""
    timestamp = ""
    dispatched_body = None

    # The SDK logs request internals (including signing material) on errors.
    # Disable logging only around this isolated request and restore it below.
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with _sdk_patches(body_mode, timestamp_mode) as body_serializer:
            try:
                http_request = client._make_http_response(
                    urlparse(endpoint).hostname,
                    request,
                    read_timeout=10,
                    connect_timeout=5,
                )
                dispatched_body = http_request.get_body()
                timestamp = request.get_headers().get("x-timestamp", "")
                if body_serializer is not None and not (
                    body_serializer.matching_calls == 2
                    and request.get_content() is body_serializer.body
                    and dispatched_body is body_serializer.body
                ):
                    raise BodyIdentityError(
                        "explicit body was not identical for signing and dispatch"
                    )
                status, response_headers, response_body, _ = (
                    http_request.get_response_object()
                )
            except BodyIdentityError:
                raise
            except Exception:
                safe_error = "DIAGNOSTIC_TRANSPORT_ERROR"
    finally:
        logging.disable(previous_disable)

    body_type = type(dispatched_body).__name__
    body_length = len(dispatched_body) if dispatched_body is not None else 0
    if not safe_error:
        safe_error = _error_code(response_body)
    return DiagnosticResult(
        python_version=platform.python_version(),
        sdk_version=_sdk_version(),
        timestamp_format_classification=_classify_timestamp(timestamp),
        body_type=body_type,
        body_length=body_length,
        http_status=status,
        sanitized_error_code=safe_error,
        request_id=_safe_value(_header(response_headers, "X-Request-Id")),
    )


def _sdk_version() -> str:
    try:
        return version("webull-openapi-python-sdk")
    except PackageNotFoundError:
        return "UNKNOWN"


def _classify_timestamp(value: object) -> str:
    text = str(value)
    if _ISO_8601_UTC.fullmatch(text):
        return "ISO_8601_UTC_SECONDS"
    if _EPOCH_MILLISECONDS.fullmatch(text):
        return "EPOCH_MILLISECONDS"
    return "UNKNOWN"


def _error_code(body: object) -> str:
    try:
        if isinstance(body, bytes):
            body = body.decode("utf-8")
        payload = json.loads(body) if body else {}
        return _safe_value(payload.get("error_code", ""))
    except (UnicodeDecodeError, ValueError, TypeError, AttributeError):
        return "UNPARSEABLE_ERROR_RESPONSE"


def _header(headers: object, name: str) -> object:
    if not hasattr(headers, "items"):
        return ""
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return value
    return ""


def _safe_value(value: object) -> str:
    text = str(value or "")
    return text if not text or _SAFE_VALUE.fullmatch(text) else "REDACTED"


__all__ = [
    "BodyIdentityError",
    "BodyMode",
    "DiagnosticResult",
    "TimestampMode",
    "build_diagnostic_api_client",
    "run_config_diagnostic",
]

"""One-process worker for a single Webull config diagnostic."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

from .diagnostic import BodyMode, DiagnosticResult, TimestampMode, run_config_diagnostic


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body", choices=[item.value for item in BodyMode], required=True)
    parser.add_argument(
        "--timestamp",
        choices=[item.value for item in TimestampMode],
        default=TimestampMode.ISO_8601_UTC.value,
        help=(
            "Diagnostic only. epoch-milliseconds is contrary to Webull's "
            "currently published ISO 8601 UTC requirement."
        ),
    )
    return parser


def _settings() -> tuple[str, str, str]:
    values = dict(os.environ)
    file_values = dotenv_values(Path(".env"))
    # Scoped market-data .env values are authoritative for this diagnostic;
    # stale process variables and legacy sandbox values cannot replace them.
    for name in (
        "WEBULL_MARKET_DATA_APP_KEY",
        "WEBULL_MARKET_DATA_APP_SECRET",
        "WEBULL_MARKET_DATA_API_BASE_URL",
    ):
        if file_values.get(name) is not None:
            values[name] = str(file_values[name])
    key = values.get("WEBULL_MARKET_DATA_APP_KEY") or values.get("WEBULL_API_KEY", "")
    secret = values.get("WEBULL_MARKET_DATA_APP_SECRET") or values.get("WEBULL_API_SECRET", "")
    endpoint = (
        values.get("WEBULL_MARKET_DATA_API_BASE_URL")
        or values.get("WEBULL_API_BASE_URL")
        or "https://api.webull.com"
    )
    return key, secret, endpoint


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    key, secret, endpoint = _settings()
    try:
        result = run_config_diagnostic(
            app_key=key,
            app_secret=secret,
            endpoint=endpoint,
            body_mode=BodyMode(args.body),
            timestamp_mode=TimestampMode(args.timestamp),
        )
    except Exception:
        result = DiagnosticResult(
            python_version=".".join(map(str, sys.version_info[:3])),
            sdk_version=_installed_sdk_version(),
            timestamp_format_classification="UNKNOWN",
            body_type="UNKNOWN",
            body_length=0,
            http_status=None,
            sanitized_error_code="DIAGNOSTIC_SETUP_ERROR",
            request_id="",
        )
    print(result.to_json())
    return 0


def _installed_sdk_version() -> str:
    try:
        from importlib.metadata import version

        return version("webull-openapi-python-sdk")
    except Exception:
        return "UNKNOWN"


if __name__ == "__main__":
    raise SystemExit(main())

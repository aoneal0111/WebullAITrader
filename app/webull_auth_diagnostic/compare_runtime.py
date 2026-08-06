"""Sanitized, same-process comparison of diagnostic and Atlas SDK clients."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from dotenv import dotenv_values
from webull.core.http.initializer.config.bean.query_config_request import (
    GetConfigRequest,
)
from webull.core.utils import common

from app.configuration import load_configuration
from app.configuration.environment import (
    MARKET_DATA_DOTENV_KEYS,
    duplicate_dotenv_keys,
    resolve_runtime_environment,
)
from app.webull.client_factories import MarketDataClientFactory
from app.webull.sdk_market_data import build_official_data_api_client

from .diagnostic import build_diagnostic_api_client


_ORIGINAL_JSON_DUMPS = common.json_dumps_compact
_ORIGINAL_TIMESTAMP = common.get_iso_8601_date


@dataclass(frozen=True, slots=True)
class SecretSummary:
    length: int
    utf8_length: int
    fingerprint: str
    has_surrounding_whitespace: bool


def _secret_summary(value: str, label: str) -> SecretSummary:
    encoded = value.encode("utf-8")
    digest = hashlib.sha256(
        f"atlas-auth-comparison-{label}\0".encode("ascii") + encoded
    ).hexdigest()
    return SecretSummary(
        len(value),
        len(encoded),
        f"sha256:{digest[:16]}" if value else "missing",
        value != value.strip(),
    )


def _logger_snapshot() -> dict[str, object]:
    result = {}
    for name in ("webull", "webull.core"):
        logger = logging.getLogger(name)
        result[name] = {
            "level": logging.getLevelName(logger.level),
            "propagate": logger.propagate,
            "handlers": [type(handler).__name__ for handler in logger.handlers],
        }
    return result


def _endpoint_map(client: object) -> dict[str, str]:
    resolver = client._endpoint_resolver._user_customized_endpoint_resolver
    return dict(resolver._endpoint_entry_map)


def _client_snapshot(
    client: object,
    *,
    configured_endpoint: str,
) -> dict[str, object]:
    request = GetConfigRequest()
    body = request.get_body_params()
    timestamp = common.get_iso_8601_date()
    parsed = urlparse(configured_endpoint)
    return {
        "app_key": asdict(_secret_summary(client._app_key, "shared-key")),
        "app_secret": asdict(
            _secret_summary(client._app_secret, "shared-secret")
        ),
        "app_key_equals_app_secret": client._app_key == client._app_secret,
        "endpoint": configured_endpoint,
        "parsed_hostname": parsed.hostname,
        "port": client._port,
        "region_id": client._region_id,
        "sdk_version": version("webull-openapi-python-sdk"),
        "python_version": platform.python_version(),
        "constructor_options": {
            "auto_retry": client._auto_retry,
            "max_retry_num": client._max_retry_num,
            "connect_timeout": client._connect_timeout,
            "read_timeout": client._read_timeout,
            "verify": client._verify,
        },
        "logger_flags": {
            "stream": client._stream_logger_set,
            "file": client._file_logger_set,
        },
        "logger_configuration": _logger_snapshot(),
        "user_agent": {
            "configured": client._user_agent,
            "extra": dict(client._extra_user_agent),
        },
        "registered_endpoint_map": _endpoint_map(client),
        "request_body": {
            "type": type(body).__name__,
            "length": len(body) if body is not None else 0,
        },
        "timestamp": {
            "classification": (
                "ISO_8601_UTC_SECONDS"
                if len(timestamp) == 20 and timestamp.endswith("Z")
                else "OTHER"
            ),
            "python_type": type(timestamp).__name__,
        },
        "signing_functions_original": {
            "json_dumps_compact": common.json_dumps_compact is _ORIGINAL_JSON_DUMPS,
            "get_iso_8601_date": common.get_iso_8601_date is _ORIGINAL_TIMESTAMP,
        },
        "cached_state": {
            "access_token_present": bool(client.get_token()),
            "token_directory_present": bool(client.get_token_dir()),
            "new_api_client": True,
        },
    }


def _origins(
    process_environment: Mapping[str, str], file_values: Mapping[str, object]
) -> dict[str, str]:
    result = {}
    for name in MARKET_DATA_DOTENV_KEYS:
        in_file = file_values.get(name) is not None
        in_process = name in process_environment
        result[name] = (
            "dotenv_overrides_process"
            if in_file and in_process
            else "dotenv"
            if in_file
            else "process"
            if in_process
            else "missing"
        )
    return result


def compare_runtime_clients(
    *,
    process_environment: Mapping[str, str] | None = None,
    dotenv_path: str | Path = ".env",
) -> dict[str, object]:
    process = dict(os.environ if process_environment is None else process_environment)
    path = Path(dotenv_path)
    file_values = dotenv_values(path) if path.is_file() else {}
    resolved = resolve_runtime_environment(process, dotenv_path=path)
    configuration = load_configuration(resolved)
    market = configuration.market_data

    # Reproduce the original successful diagnostic selector exactly. It read
    # the whole .env file, let process variables override it, and then preferred
    # the trading-scoped identity and endpoint over legacy values.
    original_diagnostic_values = {
        name: str(value)
        for name, value in file_values.items()
        if value is not None
    }
    original_diagnostic_values.update(process)
    diagnostic_key = original_diagnostic_values.get(
        "WEBULL_TRADING_APP_KEY"
    ) or original_diagnostic_values.get("WEBULL_API_KEY", "")
    diagnostic_secret = original_diagnostic_values.get(
        "WEBULL_TRADING_APP_SECRET"
    ) or original_diagnostic_values.get("WEBULL_API_SECRET", "")
    diagnostic_endpoint = original_diagnostic_values.get(
        "WEBULL_TRADING_API_BASE_URL"
    ) or original_diagnostic_values.get(
        "WEBULL_API_BASE_URL", "https://api.webull.com"
    )

    diagnostic_client = build_diagnostic_api_client(
        app_key=diagnostic_key,
        app_secret=diagnostic_secret,
        endpoint=diagnostic_endpoint,
    )
    diagnostic = _client_snapshot(
        diagnostic_client,
        configured_endpoint=diagnostic_endpoint,
    )

    atlas_arguments = {}

    def atlas_builder(**kwargs):
        atlas_arguments.update(kwargs)
        return build_official_data_api_client(**kwargs)

    atlas_factory_client = MarketDataClientFactory(market, atlas_builder).create()
    atlas = _client_snapshot(
        atlas_factory_client,
        configured_endpoint=market.api_base_url,
    )
    differences = {
        name: {
            "diagnostic_client": diagnostic[name],
            "atlas_factory_client": atlas[name],
        }
        for name in diagnostic
        if diagnostic[name] != atlas[name]
    }
    return {
        "diagnostic_client": diagnostic,
        "atlas_factory_client": atlas,
        "differences": differences,
        "credentials_identical": (
            diagnostic["app_key"] == atlas["app_key"]
            and diagnostic["app_secret"] == atlas["app_secret"]
        ),
        "request_auth_inputs_identical": all(
            diagnostic[name] == atlas[name]
            for name in (
                "app_key",
                "app_secret",
                "endpoint",
                "parsed_hostname",
                "port",
                "region_id",
                "request_body",
                "timestamp",
                "registered_endpoint_map",
                "signing_functions_original",
            )
        ),
        "atlas_factory_arguments": {
            "app_key": asdict(_secret_summary(atlas_arguments["app_key"], "shared-key")),
            "app_secret": asdict(
                _secret_summary(atlas_arguments["app_secret"], "shared-secret")
            ),
            "endpoint": atlas_arguments["endpoint"],
        },
        "environment": {
            "dotenv_path": str(path.resolve()),
            "duplicate_keys": duplicate_dotenv_keys(path) if path.is_file() else {},
            "origins": _origins(process, file_values),
            "scoped_overrides_legacy": bool(
                market.api_key
                == str(resolved.get("WEBULL_MARKET_DATA_APP_KEY", "")).strip()
                and market.api_secret
                == str(resolved.get("WEBULL_MARKET_DATA_APP_SECRET", "")).strip()
            ),
            "successful_diagnostic_identity_source": "WEBULL_TRADING_*",
            "atlas_identity_source": "WEBULL_MARKET_DATA_*",
        },
    }


def main() -> int:
    try:
        payload = compare_runtime_clients()
    except Exception as exc:
        payload = {
            "comparison_error": type(exc).__name__,
            "credentials_or_headers_exposed": False,
        }
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["compare_runtime_clients"]

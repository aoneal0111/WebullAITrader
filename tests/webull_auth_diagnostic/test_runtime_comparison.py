from __future__ import annotations

import json

import pytest
from webull.core.client import ApiClient
from webull.core.http.response import Response
from webull.core.utils import common

from app.configuration import load_configuration
from app.configuration.environment import resolve_runtime_environment
from app.webull.sdk_market_data import create_official_data_client
from app.webull_auth_diagnostic.compare_runtime import compare_runtime_clients
from app.webull_auth_diagnostic.diagnostic import BodyMode, run_config_diagnostic
from app.webull_auth_diagnostic.worker import _settings


SCOPED_ENV = """\
WEBULL_MARKET_DATA_ENVIRONMENT=PRODUCTION
WEBULL_MARKET_DATA_APP_KEY=current-production-key
WEBULL_MARKET_DATA_APP_SECRET=current-production-secret
WEBULL_MARKET_DATA_API_BASE_URL=https://api.webull.com
WEBULL_MARKET_DATA_STREAM_URL=wss://data-api.webull.com:8883/mqtt
"""


def _write_env(tmp_path, content: str = SCOPED_ENV):
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_comparison_detects_successful_diagnostic_used_different_identity(
    tmp_path,
) -> None:
    path = _write_env(
        tmp_path,
        SCOPED_ENV
        + "WEBULL_TRADING_APP_KEY=successful-trading-key\n"
        + "WEBULL_TRADING_APP_SECRET=successful-trading-secret\n"
        + "WEBULL_TRADING_API_BASE_URL=https://api.sandbox.webull.com\n",
    )
    process = {
        "WEBULL_API_KEY": "legacy-sandbox-key",
        "WEBULL_API_SECRET": "legacy-sandbox-secret",
        "WEBULL_API_BASE_URL": "https://api.sandbox.webull.com",
        "WEBULL_STREAM_URL": "wss://data-api.sandbox.webull.com:8883/mqtt",
    }

    result = compare_runtime_clients(
        process_environment=process,
        dotenv_path=path,
    )

    assert result["credentials_identical"] is False
    assert result["request_auth_inputs_identical"] is False
    assert result["environment"]["scoped_overrides_legacy"] is True
    assert result["environment"]["successful_diagnostic_identity_source"] == (
        "WEBULL_TRADING_*"
    )
    assert result["diagnostic_client"]["endpoint"] == (
        "https://api.sandbox.webull.com"
    )
    assert result["atlas_factory_client"]["endpoint"] == "https://api.webull.com"
    assert result["diagnostic_client"]["app_key_equals_app_secret"] is False
    assert result["atlas_factory_client"]["app_key_equals_app_secret"] is False
    assert result["atlas_factory_client"]["request_body"] == {
        "type": "NoneType",
        "length": 0,
    }
    serialized = json.dumps(result)
    assert "current-production-key" not in serialized
    assert "current-production-secret" not in serialized
    assert "legacy-sandbox-key" not in serialized
    assert "legacy-sandbox-secret" not in serialized
    assert "successful-trading-key" not in serialized
    assert "successful-trading-secret" not in serialized


def test_scoped_dotenv_values_override_legacy_and_stale_scoped_process(tmp_path) -> None:
    path = _write_env(tmp_path)
    stale = {
        "WEBULL_API_KEY": "legacy-key",
        "WEBULL_API_SECRET": "legacy-secret",
        "WEBULL_API_BASE_URL": "https://api.sandbox.webull.com",
        "WEBULL_STREAM_URL": "wss://data-api.sandbox.webull.com:8883/mqtt",
        "WEBULL_MARKET_DATA_APP_KEY": "stale-key",
        "WEBULL_MARKET_DATA_APP_SECRET": "stale-secret",
        "WEBULL_MARKET_DATA_API_BASE_URL": "https://stale.example",
        "WEBULL_MARKET_DATA_STREAM_URL": "wss://stale.example:8883/mqtt",
        "WEBULL_MARKET_DATA_ENVIRONMENT": "TEST",
    }

    resolved = resolve_runtime_environment(stale, dotenv_path=path)
    market = load_configuration(resolved).market_data

    assert market.api_key == "current-production-key"
    assert market.api_secret == "current-production-secret"
    assert market.api_base_url == "https://api.webull.com"
    assert market.environment.value == "PRODUCTION"


def test_diagnostic_worker_selects_market_data_not_trading_identity(
    tmp_path, monkeypatch
) -> None:
    _write_env(
        tmp_path,
        SCOPED_ENV
        + "WEBULL_TRADING_APP_KEY=working-trading-key\n"
        + "WEBULL_TRADING_APP_SECRET=working-trading-secret\n"
        + "WEBULL_TRADING_API_BASE_URL=https://api.sandbox.webull.com\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WEBULL_MARKET_DATA_APP_KEY", "stale-key")
    monkeypatch.setenv("WEBULL_MARKET_DATA_APP_SECRET", "stale-secret")
    monkeypatch.setenv("WEBULL_MARKET_DATA_API_BASE_URL", "https://stale.example")

    key, secret, endpoint = _settings()

    assert key == "current-production-key"
    assert secret == "current-production-secret"
    assert endpoint == "https://api.webull.com"


def test_dotenv_resolution_is_deterministic_and_rejects_duplicates(tmp_path) -> None:
    path = _write_env(tmp_path)
    process = {"WEBULL_MARKET_DATA_APP_KEY": "stale"}

    first = resolve_runtime_environment(process, dotenv_path=path)
    second = resolve_runtime_environment(process, dotenv_path=path)

    assert first == second
    duplicate = _write_env(
        tmp_path,
        SCOPED_ENV + "WEBULL_MARKET_DATA_APP_KEY=ambiguous\n",
    )
    with pytest.raises(ValueError, match="duplicate scoped market-data"):
        resolve_runtime_environment(process, dotenv_path=duplicate)


def test_default_atlas_loader_uses_current_scoped_dotenv_over_stale_process(
    tmp_path, monkeypatch
) -> None:
    _write_env(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WEBULL_MARKET_DATA_APP_KEY", "stale-key")
    monkeypatch.setenv("WEBULL_MARKET_DATA_APP_SECRET", "stale-secret")
    monkeypatch.setenv("WEBULL_MARKET_DATA_API_BASE_URL", "https://stale.example")
    monkeypatch.setenv(
        "WEBULL_MARKET_DATA_STREAM_URL", "wss://stale.example:8883/mqtt"
    )
    monkeypatch.setenv("WEBULL_MARKET_DATA_ENVIRONMENT", "TEST")

    market = load_configuration().market_data

    assert market.api_key == "current-production-key"
    assert market.api_secret == "current-production-secret"
    assert market.api_base_url == "https://api.webull.com"
    assert market.environment.value == "PRODUCTION"


def test_atlas_data_client_initialization_preserves_sdk_default_none_body(
    monkeypatch,
) -> None:
    captured = []

    class ConfigResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"token_check_enabled": False}

    def fake_action(self, request, signer=None):
        captured.append(request.get_body_params())
        return 200, {}, b"{}", None, ConfigResponse()

    monkeypatch.setattr(ApiClient, "_implementation_of_do_action", fake_action)

    client = create_official_data_client(
        app_key="production-key",
        app_secret="production-secret",
        endpoint="https://api.webull.com",
    )

    assert client is not None
    assert captured == [None]


def test_no_signing_patch_remains_after_diagnostic_or_comparison(
    tmp_path, monkeypatch
) -> None:
    original_json_dumps = common.json_dumps_compact
    original_timestamp = common.get_iso_8601_date
    monkeypatch.setattr(
        Response,
        "get_response_object",
        lambda self: (200, {}, b"{}", object()),
    )
    run_config_diagnostic(
        app_key="production-key",
        app_secret="production-secret",
        endpoint="https://api.webull.com",
        body_mode=BodyMode.EXPLICIT_EMPTY,
    )
    compare_runtime_clients(
        process_environment={},
        dotenv_path=_write_env(
            tmp_path,
            SCOPED_ENV
            + "WEBULL_TRADING_APP_KEY=trading-key\n"
            + "WEBULL_TRADING_APP_SECRET=trading-secret\n"
            + "WEBULL_TRADING_API_BASE_URL=https://api.sandbox.webull.com\n",
        ),
    )

    assert common.json_dumps_compact is original_json_dumps
    assert common.get_iso_8601_date is original_timestamp

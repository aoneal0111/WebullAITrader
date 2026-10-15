import inspect
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.paper_trading.protection_host_check import T0
from app.webull.protection_status_probe import ProtectionStatusProbe, event_protocol_observation, inspect_profiles


def sdk(payload=None, status=200):
    response = Mock(status_code=status, content=b"{}")
    response.json.return_value = payload if payload is not None else [{"symbol": "SPY", "status": 1}]
    client = SimpleNamespace(instrument=SimpleNamespace(get_instrument=Mock(return_value=response)))
    factory = Mock(return_value=client)
    return ProtectionStatusProbe(factory, clock=lambda: T0), factory, client, response


def test_one_unfiltered_bounded_profile_call_matches_installed_sdk_signature():
    from webull.data.quotes.instrument import Instrument
    probe, factory, client, _ = sdk()
    assert probe.calls == 0 and not factory.called
    result = probe.profiles(("SPY", "QQQ"))
    assert result.state == "RESPONSE_CAPTURED"
    assert result.schema["missing_symbols"] == ["QQQ"]
    assert result.schema["rows"][0]["status_label"] == "ACTIVE"
    assert result.schema["status_authorization"] == "NONE"
    client.instrument.get_instrument.assert_called_once_with(symbols=["SPY", "QQQ"], category="US_STOCK", page_size=5)
    inspect.signature(Instrument.get_instrument).bind(None, **client.instrument.get_instrument.call_args.kwargs)
    assert probe.profiles(("SPY",)).state == "PROBE_REQUEST_LIMIT"
    assert probe.calls == 1 and factory.call_count == 1


@pytest.mark.parametrize("symbols", [(), ("spy",), ("SPY", "SPY"), tuple("ABCDEF"),
    ("SPY,QQQ",), ("../../secret",), (None,), (["SPY"],), ["SPY"]])
def test_invalid_symbols_never_construct_client(symbols):
    probe, factory, _, _ = sdk()
    with pytest.raises(ValueError):
        probe.profiles(symbols)
    assert not factory.called and probe.calls == 0


@pytest.mark.parametrize("status,label", [(1,"ACTIVE"), ("2","INACTIVE"), (3,"HALTED"),
    ("ACTIVE","ACTIVE"), (True,"UNKNOWN"), (1.0,"UNKNOWN"), (None,"UNKNOWN"), ("active","UNKNOWN")])
def test_status_codes_are_described_without_authorizing_matching(status, label):
    schema, symbols = inspect_profiles({"data":[{"symbol":"SPY","status":status}]}, ("SPY",))
    assert symbols == ("SPY",)
    assert schema["rows"][0]["status_label"] == label
    assert schema["status_authorization"] == "NONE"


def test_missing_status_never_defaults_to_active_and_duplicate_rows_remain_visible():
    schema, _ = inspect_profiles([{"symbol":"SPY"}, {"symbol":"SPY","status":3},
                                {"symbol":"UNREQUESTED","status":1}, "bad"], ("SPY","QQQ"))
    assert schema["rows"][0]["status_present"] is False
    assert schema["rows"][0]["status_label"] == "UNKNOWN"
    assert schema["rows"][1]["status_label"] == "HALTED"
    assert schema["unexpected_rows"] == 1 and schema["malformed_rows"] == 1
    assert schema["missing_symbols"] == ["QQQ"]
    assert "UNREQUESTED" not in json.dumps(schema)


@pytest.mark.parametrize("code,label", [("OC","TRADABLE_PERMISSION"),
    ("CO","LIQUIDATE_ONLY_PERMISSION"), ("NT","NON_TRADABLE_PERMISSION")])
def test_permission_codes_remain_separate_from_exchange_halts_or_authorization(code, label):
    schema, _ = inspect_profiles([{"symbol":"SPY", "status":code}], ("SPY",))
    row = schema["rows"][0]
    assert row["status_code"] == code and row["status_label"] == label
    assert row["status_domain"] == "TRADING_PERMISSION"
    assert row["time_fields"] == {} and schema["status_authorization"] == "NONE"
    legacy, _ = inspect_profiles([{"symbol":"SPY", "status":3}], ("SPY",))
    assert legacy["rows"][0]["status_domain"] == "INSTRUMENT_STATUS"


def test_installed_event_schema_does_not_imply_instrument_stream_access():
    report = event_protocol_observation()
    assert report["state"] == "ACCOUNT_SUBSCRIPTION_SCHEMA"
    assert report["account_subscription_field"] is True
    assert report["instrument_event_list_field"] is False
    assert report["broker_instrument_schema_match"] is False
    assert report["status_channel_access"] == "UNVERIFIED"
    assert report["status_authorization"] == "NONE"


@pytest.mark.parametrize("broker", [True, False])
def test_even_compatible_protocol_shape_never_authorizes_a_status_channel(broker):
    def message(names):
        return SimpleNamespace(DESCRIPTOR=SimpleNamespace(fields_by_name=dict.fromkeys(names)))
    protocol = SimpleNamespace(
        SubscribeRequest=message(["events","timestamp"] if broker else ["unknown"]),
        SubscribeResponse=message(["type","id","eventType","position","timestamp","version","payload"]))
    report = event_protocol_observation(protocol)
    assert report["broker_instrument_schema_match"] is broker
    assert report["status_channel_access"] == "UNVERIFIED"
    assert report["status_authorization"] == "NONE"


def test_unavailable_protocol_is_not_guessed_or_reported_as_entitlement_failure():
    report = event_protocol_observation(SimpleNamespace())
    assert report["state"] == "PROTOBUF_SCHEMA_UNAVAILABLE"
    assert report["status_channel_access"] == "UNVERIFIED"


def test_bounded_time_syntax_and_no_private_provider_values_are_printed():
    schema, _ = inspect_profiles({"data":[{"symbol":"SPY","status":"private-signed-request",
        "status_timestamp":"2026-10-14T20:00:00Z","update_time":1792000000000,
        "timestamp":"private-token", "api_key":"private-key","instrument_id":"private-id",
        "message":"private-message", "updated_at":True}],"pagination_key":"private-page"}, ("SPY",))
    text = json.dumps(schema)
    assert "private" not in text
    assert schema["rows"][0]["time_fields"]["status_timestamp"]["value"] == "2026-10-14T20:00:00Z"
    assert schema["rows"][0]["time_fields"]["update_time"]["value"] == 1792000000000
    assert schema["rows"][0]["time_fields"]["timestamp"] == {"type":"str"}
    assert schema["pagination_present"] is True


@pytest.mark.parametrize("status", [401,403,417,429,500])
def test_error_body_not_decoded_and_failure_not_retried(status):
    probe, factory, _, response = sdk(status=status)
    assert probe.profiles(("SPY",)).state == f"HTTP_{status}"
    response.json.assert_not_called()
    assert probe.profiles(("SPY",)).state == "PROBE_REQUEST_LIMIT"
    assert factory.call_count == 1


def test_transport_and_initialization_exceptions_are_sanitized_and_not_retried():
    probe, factory, _, _ = sdk()
    factory.side_effect = RuntimeError("private signed request")
    result = probe.profiles(("SPY",))
    assert result.state == "SDK_REQUEST_UNAVAILABLE"
    assert "private" not in json.dumps(result.report())
    assert probe.profiles(("SPY",)).state == "PROBE_REQUEST_LIMIT"
    assert factory.call_count == 1


def test_large_and_unrecognized_responses_never_become_status_authority():
    probe, _, _, response = sdk()
    response.content = b"x" * 131073
    assert probe.profiles(("SPY",)).state == "RESPONSE_TOO_LARGE"
    response.json.assert_not_called()
    for payload in ({"items":[{"symbol":"SPY","status":1}]}, {"data":{}}, []):
        schema, symbols = inspect_profiles(payload, ("SPY",))
        assert not symbols and schema["status_authorization"] == "NONE"
        assert schema["missing_symbols"] == ["SPY"]
    schema, symbols = inspect_profiles([{"symbol":"SPY","status":1}]*101, ("SPY",))
    assert schema["state"] == "TOO_MANY_ROWS" and not symbols


def test_offline_command_does_not_load_config_or_initialize_network(monkeypatch, capsys):
    from app.paper_trading import protection_status_check as module
    import app.configuration.loader as loader
    monkeypatch.setattr(loader, "load_configuration", lambda: pytest.fail("Offline config access"))
    monkeypatch.setattr(module, "ProtectionStatusProbe", lambda *_a, **_kw: pytest.fail("Offline SDK access"))
    assert module.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["network_requests"] == 0 and result["sdk_operation_attempts"] == 0
    assert result["orders"] == 0 and not result["live_feed_validated"]


def test_online_command_uses_market_data_scope_without_ledger_or_execution(tmp_path, monkeypatch, capsys):
    from app.paper_trading import protection_status_check as module
    import app.configuration.loader as loader
    import app.webull.sdk_market_data as data
    import dotenv
    config = loader.load_configuration({"WEBULL_TRADING_ENVIRONMENT":"PAPER",
        "MARKET_DATA_ENVIRONMENT":"PRODUCTION", "WEBULL_MARKET_DATA_APP_KEY":"private-key",
        "WEBULL_MARKET_DATA_APP_SECRET":"private-secret", "WEBULL_MARKET_DATA_API_BASE_URL":"https://api.webull.com",
        "WEBULL_MARKET_DATA_STREAM_URL":"wss://quotes-stream.webullsolutions.com",
        "EXECUTION_DATABASE_PATH":str(tmp_path/"execution.db")})
    _, factory, _, _ = sdk()
    monkeypatch.setattr(data, "create_official_data_client", factory)
    monkeypatch.setattr(loader, "load_configuration", lambda: config)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda **_: None)
    assert module.main(["--online"]) == 0
    text = capsys.readouterr().out
    result = json.loads(text)
    assert result["sdk_operation_attempts"] == 1
    assert result["environment"] == "PRODUCTION"
    assert factory.call_args.kwargs["app_key"] == "private-key"
    assert factory.call_args.kwargs["timeout_seconds"] == 5
    assert result["status_authorization"] == "NONE"
    assert "private" not in text and not list(tmp_path.iterdir())


@pytest.mark.parametrize("argv", [["--symbols","SPY,SPY"], ["--online","--symbols","spy"],
                                  ["--online","--symbols","SPY,,QQQ"]])
def test_invalid_command_fails_before_sdk_construction(monkeypatch, argv):
    from app.paper_trading import protection_status_check as module
    monkeypatch.setattr(module, "ProtectionStatusProbe", lambda *_a, **_kw: pytest.fail("Invalid SDK access"))
    with pytest.raises(SystemExit) as caught:
        module.main(argv)
    assert caught.value.code == 2

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.configuration.environment import resolve_symbol_intelligence_sec_environment
from app.configuration.loader import load_symbol_intelligence_sec_configuration
from app.configuration.models import SymbolIntelligenceSECEdgarConfiguration
from app.symbol_intelligence.acquisition import SecSymbolIntelligenceAcquisitionService
from app.symbol_intelligence.composition import create_symbol_intelligence_composition
from app.symbol_intelligence.providers.sec_identity import SecIssuerIdentityResolver
from app.symbol_intelligence.providers.sec_identity import (
    AmbiguousTickerMapError, SecParserFailureCategory, SecParserRowType,
    SecTickerMapError, parse_sec_ticker_map,
)
from app.symbol_intelligence.providers.sec_transport import (
    SecAcquisitionFailure,
    SecAcquisitionFailureKind,
    SecAcquisitionResult,
    SecEdgarResponse,
)
from app.symbol_intelligence.repository import SymbolIntelligenceRepository
from app.symbol_intelligence.sec_observability import (
    BoundedJsonlSecAcquisitionObservabilitySink,
    MAX_EVENT_BYTES,
    MAX_EVENTS,
    MAX_SESSION_BYTES,
    NOOP_SEC_OBSERVABILITY,
    SecDiagnosticEndpoint,
    SecDiagnosticEvent,
    SecDiagnosticRecord,
    SecDiagnosticResult,
    create_sec_acquisition_observability_sink,
    safe_close,
)


NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)
TICKER_PAYLOAD = json.dumps({
    "data": [{"ticker": "ABC", "cik_str": "123456", "title": "Issuer"}],
}).encode()


def _config(**values):
    defaults = {
        "enabled": True,
        "user_agent": "offline-test",
        "ticker_refresh_seconds": 3600,
        "submissions_refresh_seconds": 3600,
    }
    defaults.update(values)
    return SymbolIntelligenceSECEdgarConfiguration(**defaults)


def _response(payload=TICKER_PAYLOAD):
    return SecEdgarResponse(
        SimpleNamespace(value="TICKER_MAP_EXCHANGE"), 200, payload, NOW, 1,
    )


def _parser_diagnostic(payload, endpoint="EXCHANGE_TICKER_MAP"):
    records = []
    def observe(item):
        records.append((endpoint, item))
    try:
        parse_sec_ticker_map(payload, source="SEC_EDGAR", observed_at=NOW, diagnostic=observe)
    except Exception as exc:
        return type(exc), records
    return None, records


def test_parser_failure_diagnostics_are_structural_and_closed():
    error, records = _parser_diagnostic({"data": [["123", "BAD", "SECRET_ISSUER"]]})
    assert error is SecTickerMapError
    assert records[0][1].category is SecParserFailureCategory.ROW_NOT_MAPPING
    assert records[0][1].row_type is SecParserRowType.SEQUENCE
    assert records[0][1].row_index == 0

    error, records = _parser_diagnostic({"0": {"ticker": "", "cik_str": 1, "title": "SECRET"}}, "LEGACY_TICKER_MAP_FALLBACK")
    assert error is SecTickerMapError
    assert records[0][0] == "LEGACY_TICKER_MAP_FALLBACK"
    assert records[0][1].category is SecParserFailureCategory.TICKER_BLANK

    error, records = _parser_diagnostic({"0": {"ticker": "ABC", "cik_str": "not-a-cik", "title": "SECRET"}}, "LEGACY_TICKER_MAP_FALLBACK")
    assert error is SecTickerMapError
    assert records[0][1].category is SecParserFailureCategory.CIK_NON_INTEGER

    error, records = _parser_diagnostic({"0": {"ticker": "ABC", "cik_str": 1, "title": "X" * 257}})
    assert error is SecTickerMapError
    assert records[0][1].category is SecParserFailureCategory.TITLE_TOO_LONG

    error, records = _parser_diagnostic({"0": {"ticker": "BRK.B", "cik_str": 1}, "1": {"ticker": "BRK-B", "cik_str": 2}})
    assert error is AmbiguousTickerMapError
    assert records[0][1].category is SecParserFailureCategory.NORMALIZED_TICKER_CIK_COLLISION

    error, records = _parser_diagnostic({"0": {"ticker": "ABC", "cik_str": 1}})
    assert error is None and records == []


def test_parser_failure_diagnostic_serialization_has_no_input_values(tmp_path):
    sink = BoundedJsonlSecAcquisitionObservabilitySink(tmp_path, "parser-safe")
    error, records = _parser_diagnostic({"data": [["123", "TICKER_SECRET", "ISSUER_SECRET"]]})
    assert error is SecTickerMapError
    endpoint, detail = records[0]
    sink.emit(SecDiagnosticRecord(
        SecDiagnosticEvent.TICKER_PARSE_RESULT,
        endpoint=SecDiagnosticEndpoint.EXCHANGE_TICKER_MAP,
        parser_failure_category=detail.category.value,
        parser_row_type=detail.row_type.value,
        parser_row_index=detail.row_index,
        exception_class=error.__name__,
        result=SecDiagnosticResult.FAILURE,
    ))
    text = (tmp_path / "d3-sec-acquisition-parser-safe.jsonl").read_text()
    assert "TICKER_SECRET" not in text
    assert "ISSUER_SECRET" not in text
    assert "123" not in text
    assert "SEC ticker" not in text


def test_exchange_positional_rows_use_declared_field_order():
    payload = {"fields": ["cik", "name", "ticker", "exchange"],
               "data": [[123, "Issuer", "ABC", "NASDAQ"]]}
    result = parse_sec_ticker_map(payload, source="SEC_EDGAR", observed_at=NOW)
    assert result.identities[0].normalized_symbol == "ABC"
    assert result.identities[0].cik == 123
    assert result.identities[0].issuer_name == "Issuer"
    assert result.identities[0].exchange == "NASDAQ"


def test_exchange_positional_rows_follow_changed_field_order():
    payload = {"fields": ["exchange", "ticker", "name", "cik"],
               "data": [["NASDAQ", "ABC", "Issuer", "123"]]}
    result = parse_sec_ticker_map(payload, source="SEC_EDGAR", observed_at=NOW)
    assert result.identities[0].normalized_symbol == "ABC"
    assert result.identities[0].cik == 123


def test_exchange_positional_rows_reuse_ticker_cik_and_collision_validation():
    fields = ["cik", "name", "ticker", "exchange"]
    with pytest.raises(SecTickerMapError):
        parse_sec_ticker_map({"fields": fields, "data": [[1, "Issuer", "BAD!", "NASDAQ"]]}, source="SEC_EDGAR", observed_at=NOW)
    with pytest.raises(SecTickerMapError):
        parse_sec_ticker_map({"fields": fields, "data": [["bad", "Issuer", "ABC", "NASDAQ"]]}, source="SEC_EDGAR", observed_at=NOW)
    with pytest.raises(AmbiguousTickerMapError):
        parse_sec_ticker_map({"fields": fields, "data": [[1, "One", "BRK.B", "NASDAQ"], [2, "Two", "BRK-B", "NYSE"]]}, source="SEC_EDGAR", observed_at=NOW)


@pytest.mark.parametrize("payload", [
    {"fields": ["cik", "ticker", "exchange"], "data": [[1, "ABC", "NASDAQ"]]},
    {"fields": ["cik", "ticker", "ticker", "name", "exchange"], "data": [[1, "ABC", "ABC", "Issuer", "NASDAQ"]]},
    {"fields": ["cik", "name", "ticker", "exchange"], "data": [[1, "Issuer"]]},
    {"fields": ["cik", "name", "ticker", "exchange"], "data": [{"cik": 1}]},
])
def test_exchange_positional_schema_remains_fail_closed(payload):
    with pytest.raises(SecTickerMapError):
        parse_sec_ticker_map(payload, source="SEC_EDGAR", observed_at=NOW)


def test_no_usable_ticker_record_is_omitted_without_normalizing_sentinel():
    for sentinel in ("NONE", "NONE."):
        payload = {"0": {"ticker": sentinel, "cik_str": 1, "title": "Sentinel"},
                   "1": {"ticker": "ABC", "cik_str": 2, "title": "Issuer"}}
        result = parse_sec_ticker_map(payload, source="SEC_EDGAR", observed_at=NOW)
        assert [item.normalized_symbol for item in result.identities] == ["ABC"]


@pytest.mark.parametrize("ticker_value", ["N/A"])
def test_unproven_invalid_sentinel_values_are_not_omitted(ticker_value):
    with pytest.raises(SecTickerMapError):
        parse_sec_ticker_map({"0": {"ticker": ticker_value, "cik_str": 1}}, source="SEC_EDGAR", observed_at=NOW)


@pytest.mark.parametrize("ticker_value", ["NA", "NULL", "UNKNOWN", "UNAVAILABLE"])
def test_unproven_valid_ticker_values_remain_ordinary(ticker_value):
    result = parse_sec_ticker_map({"0": {"ticker": ticker_value, "cik_str": 1}}, source="SEC_EDGAR", observed_at=NOW)
    assert result.identities[0].normalized_symbol == ticker_value


def test_no_usable_ticker_with_invalid_cik_remains_fail_closed():
    with pytest.raises(SecTickerMapError):
        parse_sec_ticker_map({"0": {"ticker": "NONE.", "cik_str": "bad"}}, source="SEC_EDGAR", observed_at=NOW)


@pytest.mark.parametrize("ticker_value", ["ABC.", "A/B", "-ABC", "ABC--DEF"])
def test_arbitrary_invalid_tickers_are_not_omitted(ticker_value):
    with pytest.raises(SecTickerMapError):
        parse_sec_ticker_map({"0": {"ticker": ticker_value, "cik_str": 1}}, source="SEC_EDGAR", observed_at=NOW)


class _Transport:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def acquire(self, request):
        self.calls.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _CollectingSink:
    def __init__(self):
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def close(self):
        return None


class _ThrowingSink:
    def emit(self, record):
        raise OSError("secret diagnostic failure")

    def close(self):
        raise OSError("secret close failure")


def _service(tmp_path, results, *, sink=None):
    repository = SymbolIntelligenceRepository(tmp_path / "si.sqlite3")
    resolver = SecIssuerIdentityResolver(repository)
    transport = _Transport(results)
    service = SecSymbolIntelligenceAcquisitionService(
        _config(), repository, transport, resolver,
    )
    if sink is not None:
        service.set_observability_sink(sink)
    return service, repository, resolver, transport


def _event(result=SecDiagnosticResult.SUCCESS):
    return SecDiagnosticRecord(
        SecDiagnosticEvent.TICKER_PARSE_RESULT,
        endpoint=SecDiagnosticEndpoint.EXCHANGE_TICKER_MAP,
        result=result,
    )


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_default_configuration_and_factory_are_noop_without_files(tmp_path):
    root = tmp_path / "must-not-exist"
    config = _config(d3_diagnostics_root=root)
    assert config.d3_diagnostics_enabled is False
    assert create_sec_acquisition_observability_sink(config) is NOOP_SEC_OBSERVABILITY
    assert not root.exists()


def test_default_production_composition_injects_noop_without_creating_diagnostics(tmp_path):
    diagnostics_root = tmp_path / "must-not-exist"
    sec = _config(
        acquisition_enabled=True,
        d3_diagnostics_root=diagnostics_root,
    )
    configuration = SimpleNamespace(
        symbol_intelligence_sec_edgar=sec,
        sec_edgar=None,
        environment="PAPER",
    )

    class Ownership:
        admission_open = True
        def __init__(self, *args, **kwargs):
            pass
        def authorize_construction(self, factory):
            return factory()
        def configure_shutdown(self, **kwargs):
            self.callbacks = kwargs
        def close(self, **kwargs):
            return True

    class Transport:
        def __init__(self, *args, **kwargs):
            pass
        def close(self):
            return None

    composition = create_symbol_intelligence_composition(
        configuration,
        activate=True,
        repository_factory=lambda path: SymbolIntelligenceRepository(tmp_path / "composition.sqlite3"),
        lease_factory=lambda path: object(),
        ownership_runtime_factory=Ownership,
        transport_factory=Transport,
    )
    assert composition is not None
    assert composition.observability is NOOP_SEC_OBSERVABILITY
    assert composition.service._observability is NOOP_SEC_OBSERVABILITY
    assert not diagnostics_root.exists()


def test_explicit_configuration_creates_one_sanitized_jsonl_session(tmp_path):
    config = load_symbol_intelligence_sec_configuration(
        resolve_symbol_intelligence_sec_environment({
            "ATLAS_SEC_EDGAR_ENABLED": "true",
            "ATLAS_SEC_EDGAR_USER_AGENT": "offline-test",
            "ATLAS_SYMBOL_INTELLIGENCE_SEC_D3_DIAGNOSTICS_ENABLED": "true",
            "ATLAS_SYMBOL_INTELLIGENCE_SEC_D3_DIAGNOSTICS_ROOT": str(tmp_path),
            "ATLAS_SYMBOL_INTELLIGENCE_SEC_D3_DIAGNOSTICS_SESSION_ID": "d3_20260917",
        }, dotenv_path=None)
    )
    sink = create_sec_acquisition_observability_sink(config)
    sink.emit(_event())
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert _lines(files[0])[0]["event"] == "TICKER_PARSE_RESULT"


def test_enabled_configuration_requires_root_and_safe_session_id():
    with pytest.raises(ValueError, match="root"):
        _config(d3_diagnostics_enabled=True, d3_diagnostics_session_id="valid")
    with pytest.raises(ValueError, match="session ID"):
        _config(
            d3_diagnostics_enabled=True,
            d3_diagnostics_root="diagnostics",
            d3_diagnostics_session_id="../unsafe",
        )


def test_event_count_bound_stops_at_512_without_raising(tmp_path):
    sink = BoundedJsonlSecAcquisitionObservabilitySink(tmp_path, "count")
    for _ in range(MAX_EVENTS + 10):
        sink.emit(_event())
    assert sink.path is not None
    assert len(sink.path.read_bytes().splitlines()) == MAX_EVENTS
    assert sink.active is False


def test_oversized_serialization_is_rejected_and_sensitive_input_is_omitted(tmp_path, monkeypatch):
    sink = BoundedJsonlSecAcquisitionObservabilitySink(tmp_path, "oversize")
    monkeypatch.setattr(
        "app.symbol_intelligence.sec_observability.json.dumps",
        lambda *args, **kwargs: "x" * MAX_EVENT_BYTES,
    )
    sink.emit(SecDiagnosticRecord(
        SecDiagnosticEvent.TICKER_PARSE_RESULT,
        exception_class="secret body headers User-Agent credentials?token=abc" * 200,
    ))
    assert sink.path is not None and sink.path.stat().st_size == 0
    assert sink.active is False


def test_session_size_never_exceeds_one_mib(tmp_path, monkeypatch):
    sink = BoundedJsonlSecAcquisitionObservabilitySink(tmp_path, "bytes")
    monkeypatch.setattr(
        "app.symbol_intelligence.sec_observability.json.dumps",
        lambda *args, **kwargs: "x" * 4000,
    )
    for _ in range(MAX_EVENTS):
        sink.emit(_event())
    assert sink.path is not None
    assert sink.path.stat().st_size <= MAX_SESSION_BYTES
    assert sink.active is False


def test_existing_sessions_are_never_deleted_or_overwritten(tmp_path):
    old = tmp_path / "d3-sec-acquisition-old.jsonl"
    old.write_bytes(b"preserved evidence\n")
    refused = BoundedJsonlSecAcquisitionObservabilitySink(tmp_path, "old")
    assert refused.active is False
    assert old.read_bytes() == b"preserved evidence\n"
    created = BoundedJsonlSecAcquisitionObservabilitySink(tmp_path, "new")
    created.emit(_event())
    assert old.read_bytes() == b"preserved evidence\n"


@pytest.mark.parametrize("failure", ["open", "write", "flush"])
def test_file_append_failures_are_contained(tmp_path, monkeypatch, failure):
    sink = BoundedJsonlSecAcquisitionObservabilitySink(tmp_path, f"failure-{failure}")
    original_open = Path.open

    class BrokenStream:
        def __enter__(self):
            if failure == "open":
                raise OSError("open failed")
            return self
        def __exit__(self, *args):
            return False
        def write(self, value):
            if failure == "write":
                raise OSError("write failed")
            return len(value)
        def flush(self):
            if failure == "flush":
                raise OSError("flush failed")
        def fileno(self):
            return 1

    def broken_open(path, mode="r", *args, **kwargs):
        if mode == "ab":
            return BrokenStream()
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", broken_open)
    sink.emit(_event())
    assert sink.active is False


def test_mkdir_and_initial_file_open_failures_are_contained(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("mkdir")))
    assert not BoundedJsonlSecAcquisitionObservabilitySink(tmp_path / "mkdir", "session").active
    monkeypatch.undo()
    original_open = Path.open
    monkeypatch.setattr(
        Path, "open",
        lambda path, mode="r", *args, **kwargs: (
            (_ for _ in ()).throw(OSError("open"))
            if mode == "xb" else original_open(path, mode, *args, **kwargs)
        ),
    )
    assert not BoundedJsonlSecAcquisitionObservabilitySink(tmp_path / "open", "session").active


def test_serialization_and_close_failures_are_contained(tmp_path):
    sink = BoundedJsonlSecAcquisitionObservabilitySink(tmp_path, "serialization")
    sink.emit(object())
    safe_close(_ThrowingSink())
    assert sink.active is False


def test_sanitized_schema_has_only_closed_fields_and_no_raw_material(tmp_path):
    sink = BoundedJsonlSecAcquisitionObservabilitySink(tmp_path, "sanitize")
    sink.emit(SecDiagnosticRecord(
        SecDiagnosticEvent.TICKER_TRANSPORT_FAILURE,
        endpoint=SecDiagnosticEndpoint.EXCHANGE_TICKER_MAP,
        attempt_count=999,
        transport_category="HTTP_CLIENT_ERROR",
        http_status=403,
        response_bytes=999999999,
        result=SecDiagnosticResult.FAILURE,
        exception_class="RuntimeError",
    ))
    assert sink.path is not None
    payload = _lines(sink.path)[0]
    assert set(payload) <= {
        "schema_version", "sequence", "observed_at", "event", "endpoint",
        "attempt_count", "transport_category", "http_status", "response_bytes",
        "json_type", "row_container_type", "row_count", "result", "exception_class",
    }
    serialized = sink.path.read_text(encoding="utf-8")
    for forbidden in ("body", "headers", "User-Agent", "credentials", "?token=", "offline-test"):
        assert forbidden not in serialized
    assert payload["attempt_count"] == 8
    assert payload["response_bytes"] == 16 * 1024 * 1024


def test_stage_events_distinguish_all_remaining_failure_classes(tmp_path):
    cases = []
    failure = SecAcquisitionFailure(
        SecAcquisitionFailureKind.TIMEOUT, None, NOW, 3,
    )
    cases.append((
        [SecAcquisitionResult(failure=failure), SecAcquisitionResult(failure=failure)],
        None, None, SecDiagnosticEvent.TICKER_TRANSPORT_FAILURE,
    ))
    cases.append((
        [SecAcquisitionResult(response=_response(b'{"data":[{"ticker":null}]}'))] * 2,
        None, None, SecDiagnosticEvent.TICKER_PARSE_RESULT,
    ))
    cases.append((
        [SecAcquisitionResult(response=_response())] * 2,
        "apply", None, SecDiagnosticEvent.TICKER_MAP_APPLY_RESULT,
    ))
    cases.append((
        [SecAcquisitionResult(response=_response())],
        None, "resolver", SecDiagnosticEvent.RESOLVER_RECOVERY_RESULT,
    ))
    cases.append((
        [SecAcquisitionResult(response=_response())] * 2,
        "source", None, SecDiagnosticEvent.SOURCE_AVAILABLE_RESULT,
    ))
    for index, (results, repository_failure, resolver_failure, expected) in enumerate(cases):
        case_root = tmp_path / str(index)
        sink = _CollectingSink()
        service, repository, resolver, _ = _service(case_root, results, sink=sink)
        if repository_failure == "apply":
            repository.apply_sec_ticker_map = lambda value: (_ for _ in ()).throw(RuntimeError("secret"))
        if repository_failure == "source":
            repository.store_source_state = lambda value: False
        if resolver_failure == "resolver":
            resolver.recover = lambda: (_ for _ in ()).throw(RuntimeError("secret"))
        service.run_once()
        assert any(
            record.event is expected and record.result is SecDiagnosticResult.FAILURE
            for record in sink.records
        )


def test_throwing_sink_cannot_change_fallback_persistence_readiness_or_callback(tmp_path):
    failure = SecAcquisitionFailure(
        SecAcquisitionFailureKind.HTTP_CLIENT_ERROR, None, NOW, 1, 403,
    )
    results = [
        SecAcquisitionResult(failure=failure),
        SecAcquisitionResult(response=_response()),
    ]
    service, repository, _, transport = _service(tmp_path, results, sink=_ThrowingSink())
    callbacks = []
    service.set_ticker_map_ready_callback(lambda: callbacks.append("ready"))
    service.run_once()
    assert [call.endpoint.value for call in transport.calls] == [
        "TICKER_MAP_EXCHANGE", "TICKER_MAP_LEGACY",
    ]
    assert repository.recover_current_identities(limit=10)
    assert repository.get_source_state("SEC_EDGAR").availability.value == "AVAILABLE"
    assert service.diagnostics.ticker_map_ready is True
    assert service.metrics.ticker_refresh_successes == 1
    assert callbacks == ["ready"]


def test_observability_does_not_change_parser_rejection_or_fallback_count(tmp_path):
    invalid = SecAcquisitionResult(response=_response(b'{"data":[]}'))
    service, repository, _, transport = _service(
        tmp_path, [invalid, invalid], sink=_ThrowingSink(),
    )
    service.run_once()
    assert len(transport.calls) == 2
    assert repository.recover_current_identities(limit=10) == ()
    assert service.diagnostics.ticker_map_ready is False
    assert service.metrics.ticker_refresh_failures == 1


def test_default_noop_does_not_perform_observability_json_classification(tmp_path, monkeypatch):
    service, repository, _, _ = _service(
        tmp_path, [SecAcquisitionResult(response=_response())],
    )
    monkeypatch.setattr(
        "app.symbol_intelligence.acquisition.classify_ticker_json",
        lambda value: (_ for _ in ()).throw(AssertionError("must stay no-op")),
    )
    service.run_once()
    assert service.diagnostics.ticker_map_ready is True
    assert repository.recover_current_identities(limit=10)

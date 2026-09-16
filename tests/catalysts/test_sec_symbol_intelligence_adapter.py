from datetime import UTC, datetime, timedelta

import pytest

from app.catalysts.models import CatalystEvidence
from app.catalysts.sec_edgar import SECEdgarCatalystProvider, SECEdgarPolicy
from app.catalysts.sec_symbol_intelligence_adapter import SecSymbolIntelligenceCatalystAdapter
from app.momentum_scanner.models import CatalystStatus, CatalystType
from app.symbol_intelligence import (
    DecayClass,
    EventType,
    IntelligenceEvent,
    SourceAvailability,
    SourceStateSnapshot,
    SymbolIntelligenceRepository,
)
from app.symbol_intelligence.providers.sec_filings import SecFilingFactNormalizer
from app.symbol_intelligence.providers.sec_identity import parse_sec_ticker_map
from app.symbol_intelligence.providers.sec_identity import (
    SecIssuerIdentity,
    SecIssuerResolution,
    SecResolutionStatus,
)


NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


def filing(*, symbol="OLD", form="8-K", accession="0000000001-01-000001", published=NOW, observed=NOW, verified=True,
           issuer_id="SEC_CIK:0000000123", document="doc.htm"):
    return IntelligenceEvent(
        event_id=f"SEC_EDGAR:{accession}", symbol=symbol,
        issuer_id=issuer_id, event_type=EventType.SEC_FILING,
        event_subtype=form, source="SEC_EDGAR", source_id=accession,
        published_at=published, observed_at=observed, verified=verified,
        decay_class=DecayClass.STRUCTURAL, source_parser_version="test",
        headline="raw description", source_reference="https://www.sec.gov/raw",
        metadata={"filing_date": published.date().isoformat(), "primary_document": document, "form": form, "accession_number": accession},
    )


class Repo:
    def __init__(self, events=(), state=SourceAvailability.AVAILABLE, resolution=SecResolutionStatus.RESOLVED):
        self.events = tuple(events)
        self.state = SourceStateSnapshot("SEC_EDGAR", state, NOW - timedelta(minutes=1)) if state is not None else None
        self.resolution = resolution

    def get_source_state(self, source):
        return self.state

    def resolve_symbol_identity(self, symbol, as_of):
        if self.resolution is not SecResolutionStatus.RESOLVED:
            return SecIssuerResolution(symbol, symbol, self.resolution, as_of=as_of)
        identity = SecIssuerIdentity(symbol, 123, "SEC_CIK:0000000123", symbol, "Issuer", None, None, "test", NOW, True)
        return SecIssuerResolution(symbol, symbol, self.resolution, identity=identity, as_of=as_of)

    def recent_sec_events_by_issuer(self, issuer_id, *, limit, fact_cutoff):
        assert issuer_id == "SEC_CIK:0000000123" and limit == 32 and fact_cutoff == NOW
        return self.events


def test_adapter_maps_true_with_legacy_contract_and_requested_symbol():
    result = SecSymbolIntelligenceCatalystAdapter(Repo((filing(),))).get_evidence("NEW", as_of=NOW)
    assert isinstance(result, CatalystEvidence)
    assert result.symbol == "NEW"
    assert result.catalyst_type is CatalystType.SEC_FILING
    assert result.status is CatalystStatus.TRUE
    assert result.headline == "SEC 8-K filing"
    assert result.source == "SEC_EDGAR"
    assert result.provider_event_id == "0000000001-01-000001"
    assert result.canonical_event_id == "sec-filing:0000000001-01-000001"
    assert result.source_url == "https://www.sec.gov/Archives/edgar/data/123/000000000101000001/doc.htm"


def test_adapter_status_health_and_missingness():
    assert SecSymbolIntelligenceCatalystAdapter(Repo((), SourceAvailability.AVAILABLE)).get_evidence("NEW", as_of=NOW).status is CatalystStatus.FALSE
    assert SecSymbolIntelligenceCatalystAdapter(Repo((), SourceAvailability.UNAVAILABLE)).get_evidence("NEW", as_of=NOW).status is CatalystStatus.UNAVAILABLE
    assert SecSymbolIntelligenceCatalystAdapter(Repo((), None)).get_evidence("NEW", as_of=NOW).status is CatalystStatus.UNKNOWN
    assert SecSymbolIntelligenceCatalystAdapter(Repo((), SourceAvailability.AVAILABLE, SecResolutionStatus.UNRESOLVED)).get_evidence("NEW", as_of=NOW).status is CatalystStatus.UNKNOWN


@pytest.mark.parametrize("form", ["6-K", "FORM 99"])
def test_unsupported_form_is_skipped(form):
    result = SecSymbolIntelligenceCatalystAdapter(Repo((filing(form=form),))).get_evidence("NEW", as_of=NOW)
    assert result.status is CatalystStatus.FALSE


def test_selection_freshness_and_point_in_time():
    stale = filing(accession="0000000001-01-000002", published=NOW - timedelta(days=4), observed=NOW - timedelta(days=4))
    older = filing(accession="0000000001-01-000003", published=NOW - timedelta(days=1), observed=NOW - timedelta(days=1))
    future = filing(accession="0000000001-01-000004", published=NOW + timedelta(minutes=1), observed=NOW + timedelta(minutes=1))
    result = SecSymbolIntelligenceCatalystAdapter(Repo((stale, older, future))).get_evidence("NEW", as_of=NOW)
    assert result.provider_event_id == "0000000001-01-000003"


def test_unverified_fact_fails_closed():
    result = SecSymbolIntelligenceCatalystAdapter(Repo((filing(verified=False),))).get_evidence("NEW", as_of=NOW)
    assert result.status is CatalystStatus.UNKNOWN


def test_repository_failure_is_safe():
    class Broken(Repo):
        def get_source_state(self, source):
            raise RuntimeError("secret should not escape")
    result = SecSymbolIntelligenceCatalystAdapter(Broken()).get_evidence("NEW", as_of=NOW)
    assert result.status is CatalystStatus.UNKNOWN


def test_historical_source_state_observed_after_cutoff_is_unknown():
    repo = Repo((filing(),))
    repo.state = SourceStateSnapshot("SEC_EDGAR", SourceAvailability.AVAILABLE, NOW + timedelta(minutes=1))
    result = SecSymbolIntelligenceCatalystAdapter(repo).get_evidence("NEW", as_of=NOW)
    assert result.status is CatalystStatus.UNKNOWN


@pytest.mark.parametrize("accession", ["", "0001-01", "0000000001/01/000001", "0000000001-01-00000", "../x", "0000000001-01-000001?x", "0000000001-01-000001#frag"])
def test_unsafe_accession_cannot_produce_positive_evidence(accession):
    result = SecSymbolIntelligenceCatalystAdapter(Repo((filing(accession=accession),))).get_evidence("NEW", as_of=NOW)
    assert result.status is not CatalystStatus.TRUE


@pytest.mark.parametrize("issuer_id", ["SEC_CIK:0", "SEC_CIK:-1", "SEC_CIK:123", "SEC_CIK:10000000000", "SEC_CIK:0000000123/evil", "arbitrary"])
def test_unsafe_cik_cannot_produce_positive_evidence(issuer_id):
    result = SecSymbolIntelligenceCatalystAdapter(Repo((filing(issuer_id=issuer_id),))).get_evidence("NEW", as_of=NOW)
    assert result.status is not CatalystStatus.TRUE


@pytest.mark.parametrize("document", ["../file.htm", "dir/file.htm", r"dir\\file.htm", "https://evil.example/x", "file.htm?x=1", "file.htm#frag"])
def test_unsafe_primary_document_cannot_produce_positive_evidence(document):
    result = SecSymbolIntelligenceCatalystAdapter(Repo((filing(document=document),))).get_evidence("NEW", as_of=NOW)
    assert result.status is not CatalystStatus.TRUE


def test_compact_accession_uses_shared_canonicalization_and_preserves_url_parity():
    event = filing(accession="000000000101000001")
    result = SecSymbolIntelligenceCatalystAdapter(Repo((event,))).get_evidence("NEW", as_of=NOW)
    assert result.status is CatalystStatus.TRUE
    assert result.provider_event_id == "0000000001-01-000001"
    assert result.source_url == "https://www.sec.gov/Archives/edgar/data/123/000000000101000001/doc.htm"


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _LegacyFixtureClient:
    def __init__(self, ticker_payload, submissions_payload):
        self._responses = [_Response(ticker_payload), _Response(submissions_payload)]

    def get(self, url, **kwargs):
        return self._responses.pop(0)


def _submissions(form="8-K", filing_date="2026-09-16", acceptance="2026-09-16T12:00:00Z"):
    return {
        "cik": "0000000123",
        "filings": {"recent": {
            "form": [form], "accessionNumber": ["0000000001-01-000001"],
            "filingDate": [filing_date], "acceptanceDateTime": [acceptance],
            "primaryDocument": ["doc.htm"],
        }},
    }


def _submissions_rows(rows):
    return {
        "cik": "0000000123",
        "filings": {"recent": {
            "form": [row[0] for row in rows],
            "accessionNumber": [row[1] for row in rows],
            "filingDate": [row[2] for row in rows],
            "acceptanceDateTime": [row[3] for row in rows],
            "primaryDocument": ["doc.htm" for _ in rows],
        }},
    }


def _direct_results(tmp_path, submissions):
    ticker_payload = {"0": {"ticker": "NEW", "cik_str": 123}}
    legacy = SECEdgarCatalystProvider(
        SECEdgarPolicy(user_agent="Atlas test"),
        client=_LegacyFixtureClient(ticker_payload, submissions), sleep=lambda _: None,
    )
    legacy_result = legacy.get_evidence("NEW", NOW)
    repo = SymbolIntelligenceRepository(tmp_path / "repo.sqlite3")
    repo.apply_sec_ticker_map(parse_sec_ticker_map(ticker_payload, source="SEC_EDGAR", observed_at=NOW))
    identity = repo.resolve_symbol_identity("NEW", NOW).identity
    normalized = SecFilingFactNormalizer().normalize(identity, submissions, NOW)
    assert normalized.failure is None
    repo.append_evidence(normalized.events)
    repo.store_source_state(SourceStateSnapshot("SEC_EDGAR", SourceAvailability.AVAILABLE, NOW))
    adapter_result = SecSymbolIntelligenceCatalystAdapter(repo).get_evidence("NEW", as_of=NOW)
    return legacy_result, adapter_result


@pytest.mark.parametrize("form", ["8-K", "8-K/A", "S-1", "S-3", "424B5", "10-Q", "10-K", "SC 13D"])
def test_direct_legacy_parity_supported_forms(tmp_path, form):
    legacy_result, adapter_result = _direct_results(tmp_path, _submissions(form=form))
    assert adapter_result == legacy_result


@pytest.mark.parametrize("filing_date", ["2026-09-14", "2026-09-13", "2026-09-12"])
def test_direct_legacy_parity_freshness_boundaries(tmp_path, filing_date):
    legacy_result, adapter_result = _direct_results(tmp_path, _submissions(filing_date=filing_date))
    assert adapter_result == legacy_result


def test_direct_legacy_parity_skips_unsupported_newest(tmp_path):
    submissions = _submissions_rows([
        ("FORM 99", "0000000001-01-000002", "2026-09-16", "2026-09-16T12:00:00Z"),
        ("8-K", "0000000001-01-000001", "2026-09-15", "2026-09-15T12:00:00Z"),
    ])
    legacy_result, adapter_result = _direct_results(tmp_path, submissions)
    assert adapter_result == legacy_result
    assert adapter_result.status is CatalystStatus.TRUE
    assert adapter_result.provider_event_id == "0000000001-01-000001"


def test_direct_legacy_and_adapter_parity_for_fresh_8k(tmp_path):
    ticker_payload = {"0": {"ticker": "NEW", "cik_str": 123}}
    submissions = _submissions()
    legacy = SECEdgarCatalystProvider(
        SECEdgarPolicy(user_agent="Atlas test"),
        client=_LegacyFixtureClient(ticker_payload, submissions),
        sleep=lambda _: None,
    )
    legacy_result = legacy.get_evidence("NEW", NOW)

    repo = SymbolIntelligenceRepository(tmp_path / "repo.sqlite3")
    ticker_map = parse_sec_ticker_map(ticker_payload, source="SEC_EDGAR", observed_at=NOW)
    assert repo.apply_sec_ticker_map(ticker_map)
    identity = repo.resolve_symbol_identity("NEW", NOW).identity
    normalized = SecFilingFactNormalizer().normalize(identity, submissions, NOW)
    assert normalized.failure is None and len(normalized.events) == 1
    assert repo.append_evidence(normalized.events).inserted == 1
    assert repo.store_source_state(SourceStateSnapshot("SEC_EDGAR", SourceAvailability.AVAILABLE, NOW))
    adapter_result = SecSymbolIntelligenceCatalystAdapter(repo).get_evidence("NEW", as_of=NOW)

    assert adapter_result == legacy_result


@pytest.mark.parametrize("form", ["6-K"])
def test_direct_legacy_and_adapter_parity_for_unsupported_6k(tmp_path, form):
    ticker_payload = {"0": {"ticker": "NEW", "cik_str": 123}}
    submissions = _submissions(form=form)
    legacy = SECEdgarCatalystProvider(
        SECEdgarPolicy(user_agent="Atlas test"),
        client=_LegacyFixtureClient(ticker_payload, submissions),
        sleep=lambda _: None,
    )
    legacy_result = legacy.get_evidence("NEW", NOW)
    repo = SymbolIntelligenceRepository(tmp_path / "repo.sqlite3")
    ticker_map = parse_sec_ticker_map(ticker_payload, source="SEC_EDGAR", observed_at=NOW)
    repo.apply_sec_ticker_map(ticker_map)
    identity = repo.resolve_symbol_identity("NEW", NOW).identity
    normalized = SecFilingFactNormalizer().normalize(identity, submissions, NOW)
    assert repo.append_evidence(normalized.events).inserted == 1
    repo.store_source_state(SourceStateSnapshot("SEC_EDGAR", SourceAvailability.AVAILABLE, NOW))
    adapter_result = SecSymbolIntelligenceCatalystAdapter(repo).get_evidence("NEW", as_of=NOW)
    assert legacy_result.status is CatalystStatus.FALSE
    assert adapter_result.status is CatalystStatus.FALSE
    assert (adapter_result.symbol, adapter_result.catalyst_type, adapter_result.status, adapter_result.source) == (
        legacy_result.symbol, legacy_result.catalyst_type, legacy_result.status, legacy_result.source,
    )

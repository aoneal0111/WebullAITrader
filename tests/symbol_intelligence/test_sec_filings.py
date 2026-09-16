from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

import pytest

from app.symbol_intelligence.models import EventType
from app.symbol_intelligence.repository import SymbolIntelligenceRepository
from app.symbol_intelligence.providers.sec_filings import (
    SEC_SUBMISSIONS_PARSER_VERSION,
    SecFilingFactNormalizer,
    SecFilingNormalizationFailureKind,
    normalize_accession,
)
from app.symbol_intelligence.providers.sec_identity import SecIssuerIdentity, sec_issuer_id


OBSERVED = datetime(2026, 9, 15, 20, tzinfo=UTC)


def identity(symbol="ABC", cik=123456):
    return SecIssuerIdentity(symbol, cik, sec_issuer_id(cik), symbol, "Issuer", None, None, "rev", OBSERVED, True)


def payload(*, cik=123456, forms=("8-K",), accepted=None, accessions=None, documents=None, **extra):
    count = len(forms)
    accepted = accepted or ["2026-09-15T19:00:00Z"] * count
    accessions = accessions or [f"0001234567-26-{index + 1:06d}" for index in range(count)]
    documents = documents or ["filing.htm"] * count
    recent = {
        "accessionNumber": list(accessions), "filingDate": ["2026-09-15"] * count,
        "acceptanceDateTime": list(accepted), "form": list(forms), "primaryDocument": list(documents),
    }
    recent.update(extra)
    return {"cik": str(cik), "filings": {"recent": recent, "files": [{"name": "old.json"}]}}


def normalize(value, payload_value=None, observed=OBSERVED):
    return value.normalize(identity(), payload_value or payload(), observed)


def test_valid_filing_contract_and_metadata():
    result = normalize(SecFilingFactNormalizer(), payload(items=[["1.01", "2.02"]], isXBRL=[True]))
    event = result.events[0]
    assert result.failure is None and result.diagnostics.rows_emitted == 1
    assert event.source == "SEC_EDGAR" and event.source_id == "0001234567-26-000001"
    assert event.event_type is EventType.SEC_FILING and event.event_subtype == "8-K"
    assert event.symbol == "ABC" and event.issuer_id == "SEC_CIK:0000123456"
    assert event.source_parser_version == SEC_SUBMISSIONS_PARSER_VERSION
    assert event.metadata["item_codes"] == ("1.01", "2.02")


@pytest.mark.parametrize("form", ["8-K", "8-K/A", "6-K", "6-K/A", "S-1", "S-1/A", "S-3", "S-3/A", "424B5", "EFFECT", "10-Q", "10-Q/A", "10-K", "10-K/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"])
def test_supported_forms(form):
    assert normalize(SecFilingFactNormalizer(), payload(forms=(form,))).events[0].event_subtype == form


def test_rows_are_sorted_and_duplicate_identical_accession_deduplicated():
    p = payload(forms=("10-K", "8-K"), accepted=["2026-09-15T18:00:00Z", "2026-09-15T19:00:00Z"], accessions=["0001234567-26-000002", "0001234567-26-000001"])
    result = normalize(SecFilingFactNormalizer(), p)
    assert [e.source_id for e in result.events] == ["0001234567-26-000002", "0001234567-26-000001"]

    p = payload(accessions=["0001234567-26-000001", "0001234567-26-000001"], forms=("8-K", "8-K"))
    assert len(normalize(SecFilingFactNormalizer(), p).events) == 1


def test_conflicting_duplicate_accession_fails_closed():
    p = payload(accessions=["0001234567-26-000001", "0001234567-26-000001"], forms=("8-K", "10-K"))
    result = normalize(SecFilingFactNormalizer(), p)
    assert result.failure is SecFilingNormalizationFailureKind.DUPLICATE_ACCESSION_CONFLICT
    assert result.events == ()


@pytest.mark.parametrize("kind,p", [
    (SecFilingNormalizationFailureKind.CIK_MISMATCH, payload(cik=999)),
    (SecFilingNormalizationFailureKind.INVALID_ACCEPTANCE_TIME, payload(accepted=[""])),
    (SecFilingNormalizationFailureKind.INVALID_ACCEPTANCE_TIME, payload(accepted=["2026-09-16T00:00:00Z"])),
    (SecFilingNormalizationFailureKind.UNSUPPORTED_FORM, payload(forms=("11-K",))),
    (SecFilingNormalizationFailureKind.INVALID_ACCESSION, payload(accessions=["bad"])),
])
def test_rejections_are_bounded_and_emit_no_bad_fact(kind, p):
    result = normalize(SecFilingFactNormalizer(), p)
    assert result.events == ()
    if kind is SecFilingNormalizationFailureKind.CIK_MISMATCH:
        assert result.failure is kind
    else:
        assert result.failure is None and result.diagnostics.rejection_counts[0][0] == kind.value


def test_identity_unresolved_and_misaligned_arrays():
    unresolved = SecFilingFactNormalizer().normalize(None, payload(), OBSERVED)
    assert unresolved.failure is SecFilingNormalizationFailureKind.IDENTITY_UNRESOLVED
    p = payload()
    p["filings"]["recent"]["form"] = []
    result = normalize(SecFilingFactNormalizer(), p)
    assert result.failure is SecFilingNormalizationFailureKind.MISALIGNED_ARRAYS


def test_optional_alignment_and_row_limit():
    p = payload(reportDate=[])
    assert len(normalize(SecFilingFactNormalizer(), p).events) == 1
    p = payload(reportDate=["2026-09-15", "2026-09-15"])
    assert normalize(SecFilingFactNormalizer(), p).failure is SecFilingNormalizationFailureKind.MISALIGNED_ARRAYS
    p = payload(forms=("8-K", "8-K"))
    assert SecFilingFactNormalizer(recent_row_limit=1).normalize(identity(), p, OBSERVED).failure is SecFilingNormalizationFailureKind.ROW_LIMIT_EXCEEDED


def test_safe_reference_is_optional_and_unsafe_document_is_not_fetched():
    result = normalize(SecFilingFactNormalizer(), payload(documents=["../secret.htm"]))
    assert len(result.events) == 1 and result.events[0].source_reference is None


def test_compact_accession_normalizes_without_fuzzy_matching():
    assert normalize_accession("000123456726000001") == "0001234567-26-000001"
    with pytest.raises(ValueError):
        normalize_accession("0001234567-26-00001")


def test_bytes_payload_and_historical_files_are_not_processed():
    result = normalize(SecFilingFactNormalizer(), json.dumps(payload()).encode())
    assert len(result.events) == 1


def test_valid_offline_submissions_fixture():
    fixture = Path(__file__).parent / "fixtures" / "sec_edgar" / "submissions_valid.json"
    result = normalize(SecFilingFactNormalizer(), fixture.read_bytes())
    assert [event.event_subtype for event in result.events] == ["8-K", "10-Q"]


def test_multi_symbol_context_emits_one_canonical_identity_fact():
    result = SecFilingFactNormalizer().normalize(identity("BRK-B"), payload(), OBSERVED)
    assert len(result.events) == 1 and result.events[0].symbol == "BRK-B"


def test_repository_dedupes_repeat_acquisition_and_preserves_first_observation(tmp_path):
    repository = SymbolIntelligenceRepository(tmp_path / "facts.sqlite3")
    first = normalize(SecFilingFactNormalizer(), payload(), OBSERVED).events
    later = normalize(SecFilingFactNormalizer(), payload(), OBSERVED + timedelta(hours=1)).events
    assert repository.append_evidence(first).inserted == 1
    assert repository.append_evidence(later).deduplicated == 1
    stored = repository.recent_events("ABC", limit=10)
    assert len(stored) == 1 and stored[0].event_id == first[0].event_id
    assert stored[0].observed_at == OBSERVED

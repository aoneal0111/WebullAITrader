from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.crypto_research import (
    CryptoAssetIdentityRegistry,
    CryptoCatalystAggregator,
    CryptoCatalystStatus,
    CryptoNetworkIdentity,
    CryptoNativeAssetIdentity,
    CryptoPairIdentity,
    CryptoProjectIdentity,
    FederalRegisterFailureKind,
    FederalRegisterPolicy,
    FederalRegisterProvider,
    FederalRegisterProviderError,
)


T0 = datetime(2026, 9, 8, 15, tzinfo=UTC)


def registry() -> CryptoAssetIdentityRegistry:
    btc_network = CryptoNetworkIdentity("bitcoin", "Bitcoin", "UTXO", "BTC")
    btc_project = CryptoProjectIdentity("bitcoin", "Bitcoin", "BTC", token_ids=("btc",))
    btc = CryptoNativeAssetIdentity("btc", "bitcoin", "BTC", "Bitcoin", project_id="bitcoin")
    pair = CryptoPairIdentity("BTC/USD", "BTCUSD", base_asset_id="btc")
    return CryptoAssetIdentityRegistry(projects=(btc_project,), networks=(btc_network,), native_assets=(btc,), pairs=(pair,))


def document(number="2026-17183", *, kind="Proposed Rule", publication="2026-09-08", effective=None, correction=None, title="Regulation Crypto Assets"):
    return {
        "document_number": number,
        "type": kind,
        "title": title,
        "abstract": "A bounded regulatory fixture.",
        "agencies": [{"id": 466, "name": "Securities and Exchange Commission", "raw_name": "SEC"}],
        "publication_date": publication,
        "effective_on": effective,
        "signing_date": None,
        "comments_close_on": "2026-10-08" if kind == "Proposed Rule" else None,
        "docket_ids": ["SEC-2026-0001"],
        "regulation_id_numbers": ["RIN-0000-AA00"],
        "html_url": f"https://www.federalregister.gov/documents/2026/09/08/{number}/fixture",
        "pdf_url": f"https://www.govinfo.gov/content/pkg/FR-2026-09-08/pdf/{number}.pdf",
        "correction_of": correction,
    }


class FakeTransport:
    def __init__(self, pages, error=None):
        self.pages = pages if isinstance(pages, list) else [pages]
        self.error = error
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        if self.error:
            raise self.error
        index = int(params["page"]) - 1
        return self.pages[min(index, len(self.pages) - 1)]


def provider(transport, *, policy=None, assets=None, multi=()):
    return FederalRegisterProvider(
        policy or FederalRegisterPolicy(enabled=True, maximum_pages=2, per_page=2, maximum_retries=0),
        transport=transport,
        identity_registry=registry(),
        document_asset_ids=assets or {},
        multi_asset_documents=frozenset(multi),
    )


def test_disabled_by_default_makes_no_request():
    transport = FakeTransport({"results": [document()]})
    instance = FederalRegisterProvider(transport=transport)
    assert instance.fetch_since(T0, observed_at=T0) == ()
    assert transport.calls == []


def test_real_schema_maps_document_number_agency_and_date_only_publication():
    transport = FakeTransport({"count": 1, "results": [document()]})
    item = provider(transport, multi=("2026-17183",)).fetch_since(T0.replace(hour=0), observed_at=T0)[0]
    assert item.provider_id == "FEDERAL_REGISTER"
    assert item.provider_event_id == "2026-17183"
    assert item.published_at == datetime(2026, 9, 8, tzinfo=UTC)
    assert item.association_confidence.value == "MULTI_ASSET"
    assert item.event_type.value == "REGULATORY_ACTION"
    assert item.status is CryptoCatalystStatus.ANNOUNCED


def test_explicit_asset_mapping_is_exact_and_never_text_inferred():
    item = provider(FakeTransport({"results": [document(title="Unrelated wording")]}), assets={"2026-17183": ("btc",)}).fetch_since(T0.replace(hour=0), observed_at=T0)[0]
    assert item.association_confidence.value == "EXACT"
    assert item.associated_pairs[0].pair.canonical_pair == "BTC/USD"


def test_future_effective_rule_is_scheduled_but_cutoff_eligible():
    item = provider(FakeTransport({"results": [document(kind="Rule", effective="2026-10-01")] }), multi=("2026-17183",)).fetch_since(T0.replace(hour=0), observed_at=T0)[0]
    assert item.status is CryptoCatalystStatus.SCHEDULED
    assert item.effective_at == datetime(2026, 10, 1, tzinfo=UTC)


def test_effective_rule_becomes_active():
    item = provider(FakeTransport({"results": [document(kind="Rule", effective="2026-09-01")] }), multi=("2026-17183",)).fetch_since(T0.replace(hour=0), observed_at=T0)[0]
    assert item.status is CryptoCatalystStatus.ACTIVE


def test_correction_is_revision_and_supersedes_explicit_document():
    item = provider(FakeTransport({"results": [document(number="2026-17184", kind="Correction", correction="2026-17183")] }), multi=("2026-17184",)).fetch_since(T0.replace(hour=0), observed_at=T0)[0]
    assert item.revision == 2
    assert item.supersedes == "2026-17183"
    assert item.status is CryptoCatalystStatus.REVISED


def test_duplicate_document_number_across_pages_is_suppressed():
    payload = {"results": [document()]}
    instance = provider(FakeTransport([payload, payload]), policy=FederalRegisterPolicy(enabled=True, maximum_pages=2, per_page=1, maximum_retries=0), multi=("2026-17183",))
    items = instance.fetch_since(T0.replace(hour=0), observed_at=T0)
    assert len(items) == 1
    assert instance.metrics.federal_register_duplicates_suppressed == 1


def test_same_title_different_document_numbers_remain_distinct():
    payload = {"results": [document(number="2026-17183"), document(number="2026-17185")]}
    instance = provider(FakeTransport(payload), multi=("2026-17183", "2026-17185"))
    assert len(instance.fetch_since(T0.replace(hour=0), observed_at=T0)) == 2


def test_cutoff_rejects_future_publication():
    payload = {"results": [document(publication="2026-09-09")]}
    assert provider(FakeTransport(payload), multi=("2026-17183",)).fetch_since(T0.replace(hour=0), observed_at=T0) == ()


@pytest.mark.parametrize("kind", list(FederalRegisterFailureKind))
def test_failures_are_contained(kind):
    error = FederalRegisterProviderError(kind, "sanitized failure", status_code=429 if kind is FederalRegisterFailureKind.RATE_LIMITED else None)
    instance = provider(FakeTransport({}, error=error))
    assert instance.fetch_since(T0.replace(hour=0), observed_at=T0) == ()
    assert instance.metrics.federal_register_requests_failed == 1


def test_aggregator_and_authority_flags():
    item = provider(FakeTransport({"results": [document()]}), multi=("2026-17183",)).fetch_since(T0.replace(hour=0), observed_at=T0)[0]
    aggregate = CryptoCatalystAggregator().aggregate((item, item), decision_cutoff=T0)
    assert len(aggregate.events) == 1
    data = item.to_dict()
    assert data["research_only"] is True
    assert data["selection_authorized"] is False
    assert data["execution_authorized"] is False


def test_replay_is_deterministic_and_endpoint_is_bounded():
    payload = {"count": 1, "results": [document()]}
    first = provider(FakeTransport(payload), multi=("2026-17183",)).fetch_since(T0.replace(hour=0), observed_at=T0)
    second = provider(FakeTransport(payload), multi=("2026-17183",)).fetch_since(T0.replace(hour=0), observed_at=T0)
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]


def test_proposed_rule_is_not_final_rule():
    item = provider(FakeTransport({"results": [document(kind="Proposed Rule")]}), multi=("2026-17183",)).fetch_since(T0.replace(hour=0), observed_at=T0)[0]
    assert item.status is CryptoCatalystStatus.ANNOUNCED
    assert item.event_type.value == "REGULATORY_ACTION"

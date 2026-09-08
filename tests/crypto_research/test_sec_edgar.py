from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.crypto_research import (
    CryptoAssetIdentityRegistry,
    CryptoAssociationConfidence,
    CryptoCatalystAggregator,
    CryptoCatalystType,
    CryptoNativeAssetIdentity,
    CryptoNetworkIdentity,
    CryptoPairIdentity,
    CryptoProjectIdentity,
    SECETFProductIdentity,
    SECCryptoFailureKind,
    SECCryptoProviderError,
    SECCryptoEdgarProvider,
    SECProviderPolicy,
)


T0 = datetime(2026, 9, 8, 15, tzinfo=UTC)


def submissions(*, form="S-1", accession="0000000001-26-000001", accepted="2026-09-08T14:00:00Z", filing_date="2026-09-08", document="fund.htm", extra=None):
    recent = {
        "form": [form], "filingDate": [filing_date], "acceptanceDateTime": [accepted],
        "accessionNumber": [accession], "primaryDocument": [document], "reportDate": [""] ,
    }
    if extra:
        for key, value in extra.items():
            recent[key] = value
    return {"name": "Verified Crypto Trust", "cik": "0000000001", "filings": {"recent": recent}}


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload = payload or submissions()
        self.error = error
        self.calls = []

    def get(self, url, *, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        if self.error:
            raise self.error
        return self.payload


def registry():
    btc_network = CryptoNetworkIdentity("bitcoin", "Bitcoin", "UTXO", "BTC")
    eth_network = CryptoNetworkIdentity("ethereum", "Ethereum", "EVM", "ETH", chain_id=1)
    btc_project = CryptoProjectIdentity("bitcoin", "Bitcoin", "BTC", token_ids=("btc",))
    eth_project = CryptoProjectIdentity("ethereum", "Ethereum", "ETH", token_ids=("eth",))
    btc = CryptoNativeAssetIdentity("btc", "bitcoin", "BTC", "Bitcoin", project_id="bitcoin")
    eth = CryptoNativeAssetIdentity("eth", "ethereum", "ETH", "Ethereum", project_id="ethereum")
    pairs = (CryptoPairIdentity("BTC/USD", "BTCUSD", base_asset_id="btc"), CryptoPairIdentity("ETH/USD", "ETHUSD", base_asset_id="eth"))
    return CryptoAssetIdentityRegistry(projects=(btc_project, eth_project), networks=(btc_network, eth_network), native_assets=(btc, eth), pairs=pairs)


def provider(transport, *, products=None, enabled=True, identity=True):
    return SECCryptoEdgarProvider(
        SECProviderPolicy(user_agent="Atlas tests contact@example.invalid", enabled=enabled, maximum_retries=0),
        products=tuple(products or (SECETFProductIdentity("btc-etf", "Verified Crypto Trust", 1, "Bitcoin Trust", ("btc",), "BTCX"),)),
        transport=transport, identity_registry=registry() if identity else None,
    )


def test_disabled_by_default_makes_no_request():
    transport = FakeTransport()
    instance = SECCryptoEdgarProvider(SECProviderPolicy(user_agent="Atlas tests contact@example.invalid"), products=(), transport=transport)
    assert instance.fetch_since(T0 - timedelta(days=1), observed_at=T0) == ()
    assert transport.calls == []


def test_accession_and_acceptance_timestamp_map_to_crypto_evidence():
    item = provider(FakeTransport(submissions())).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.provider_id == "SEC_EDGAR"
    assert item.provider_event_id == "0000000001-26-000001"
    assert item.event_type is CryptoCatalystType.ETF_FILING
    assert item.published_at == datetime(2026, 9, 8, 14, tzinfo=UTC)
    assert item.association_confidence is CryptoAssociationConfidence.EXACT
    assert item.associated_pairs[0].pair.canonical_pair == "BTC/USD"


def test_effect_is_approval_not_every_filing():
    item = provider(FakeTransport(submissions(form="EFFECT"))).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.event_type is CryptoCatalystType.ETF_APPROVAL
    assert item.status.value == "COMPLETED"


@pytest.mark.parametrize("form", ["424B3", "424I", "POS AM", "FWP"])
def test_live_observed_etf_forms_are_filing_evidence(form):
    item = provider(FakeTransport(submissions(form=form))).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.event_type is CryptoCatalystType.ETF_FILING


def test_periodic_issuer_disclosure_is_regulatory_action():
    item = provider(FakeTransport(submissions(form="10-Q"))).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.event_type is CryptoCatalystType.REGULATORY_ACTION


def test_effect_archive_path_is_safe_and_preserved():
    item = provider(FakeTransport(submissions(form="EFFECT", document="xslEFFECTX01/primary_doc.xml"))).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.source_url is not None
    assert "xslEFFECTX01/primary_doc.xml" in item.source_url


def test_amendment_is_distinct_revision():
    item = provider(FakeTransport(submissions(form="S-1/A"))).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.revision == 2
    assert item.event_identity != ""


def test_future_acceptance_is_rejected_at_cutoff():
    item = provider(FakeTransport(submissions(accepted="2026-09-08T16:00:00Z"))).fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert item == ()


def test_filing_date_fallback_is_midnight_when_acceptance_missing():
    payload = submissions(accepted=None)
    payload["filings"]["recent"]["acceptanceDateTime"] = [""]
    item = provider(FakeTransport(payload)).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.published_at == datetime(2026, 9, 8, tzinfo=UTC)


def test_multi_asset_product_is_explicitly_multi_asset():
    product = SECETFProductIdentity("basket", "Verified Crypto Trust", 1, "Digital Asset Basket", ("btc", "eth"))
    item = provider(FakeTransport(submissions()), products=(product,)).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.association_confidence is CryptoAssociationConfidence.MULTI_ASSET
    assert {pair.pair.canonical_pair for pair in item.associated_pairs} == {"BTC/USD", "ETH/USD"}


def test_missing_identity_remains_unresolved():
    product = SECETFProductIdentity("unknown", "Verified Crypto Trust", 1, "Unknown Trust", ("unknown-asset",))
    item = provider(FakeTransport(submissions()), products=(product,), identity=False).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.association_confidence is CryptoAssociationConfidence.UNRESOLVED
    assert item.associated_pairs == ()


def test_duplicate_accession_across_products_is_suppressed():
    products = (
        SECETFProductIdentity("btc-etf", "Verified Crypto Trust", 1, "Bitcoin Trust", ("btc",)),
        SECETFProductIdentity("btc-etf-copy", "Verified Crypto Trust", 1, "Bitcoin Trust", ("btc",)),
    )
    instance = provider(FakeTransport(submissions()), products=products)
    items = instance.fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert len(items) == 1
    assert instance.metrics.sec_crypto_duplicates_suppressed == 1


@pytest.mark.parametrize("kind", list(SECCryptoFailureKind))
def test_failures_are_sanitized(kind):
    error = SECCryptoProviderError(kind, "sanitized SEC failure", status_code=429 if kind is SECCryptoFailureKind.RATE_LIMITED else None)
    instance = provider(FakeTransport(error=error))
    assert instance.fetch_since(T0 - timedelta(days=1), observed_at=T0) == ()
    assert instance.metrics.sec_crypto_requests_failed == 1


def test_aggregator_integration_and_authority_flags():
    item = provider(FakeTransport(submissions())).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    aggregate = CryptoCatalystAggregator().aggregate((item, item), decision_cutoff=T0)
    assert len(aggregate.events) == 1
    payload = item.to_dict()
    assert payload["research_only"] is True
    assert payload["selection_authorized"] is False
    assert payload["execution_authorized"] is False


def test_user_agent_and_submissions_endpoint_are_used():
    transport = FakeTransport(submissions())
    provider(transport).fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert transport.calls[0][0].startswith("https://data.sec.gov/submissions/CIK")
    assert transport.calls[0][1]["User-Agent"].endswith("contact@example.invalid")


def test_identical_fixture_replays_identically_without_wall_clock_dependency():
    first = provider(FakeTransport(submissions())).fetch_since(T0 - timedelta(days=1), observed_at=T0)
    second = provider(FakeTransport(submissions())).fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]

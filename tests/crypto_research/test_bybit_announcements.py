from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.crypto_research import (
    CryptoAssetIdentityRegistry,
    CryptoAssociationConfidence,
    CryptoCatalystAggregator,
    CryptoCatalystType,
    CryptoContractTokenIdentity,
    CryptoNativeAssetIdentity,
    CryptoNetworkIdentity,
    CryptoPairIdentity,
    CryptoProjectIdentity,
    CryptoProviderAssetReference,
    BybitAnnouncementPolicy,
    BybitAnnouncementsProvider,
    BybitFailureKind,
    BybitProviderError,
)


T0 = datetime(2026, 9, 8, 12, tzinfo=UTC)


def row(*, event="arb-1", type_key="new_crypto", title="New Listing: Arbitrum (ARB)", tag="ARB", publish=None, effective=None, url=None, updated=None):
    return {
        "title": title,
        "description": "Bybit announcement fixture",
        "type": {"title": "New Listings", "key": type_key},
        "tags": [tag] if tag else [],
        "url": url or f"https://announcements.bybit.com/en-US/article/{event}/",
        "dateTimestamp": int(((publish or T0 - timedelta(minutes=30)).timestamp()) * 1000),
        "publishTime": int(((publish or T0 - timedelta(minutes=30)).timestamp()) * 1000),
        "startDateTimestamp": int(effective.timestamp() * 1000) if effective else None,
        "endDateTimestamp": int((effective + timedelta(hours=1)).timestamp() * 1000) if effective else None,
        "updatedAt": int(updated.timestamp() * 1000) if updated else None,
    }


def envelope(rows, *, total=None):
    return {"retCode": 0, "retMsg": "OK", "result": {"total": total if total is not None else len(rows), "list": rows}, "retExtInfo": {}, "time": int(T0.timestamp() * 1000)}


class FakeTransport:
    def __init__(self, pages=None, error=None):
        self.pages = pages or {1: envelope([])}
        self.error = error
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        if self.error:
            raise self.error
        return self.pages.get(params["page"], envelope([]))


def identity_registry():
    network = CryptoNetworkIdentity("arbitrum", "Arbitrum", "EVM", "ARB", chain_id=42161)
    project = CryptoProjectIdentity("arbitrum", "Arbitrum", "ARB", token_ids=("arb-native",))
    asset = CryptoNativeAssetIdentity(
        "arb-native", "arbitrum", "ARB", "Arbitrum", project_id="arbitrum",
        provider_references=(CryptoProviderAssetReference("BYBIT", "ARB", "ARB"),),
    )
    pair = CryptoPairIdentity("ARB/USD", "ARBUSD", "webull-arb", base_asset_id="arb-native")
    return CryptoAssetIdentityRegistry(
        projects=(project,), networks=(network,), native_assets=(asset,), pairs=(pair,),
        provider_references=(CryptoProviderAssetReference("BYBIT", "ARB", "ARB"),),
    )


def provider(transport, *, enabled=True, registry=None, maximum_pages=2):
    return BybitAnnouncementsProvider(
        BybitAnnouncementPolicy(enabled=enabled, maximum_pages=maximum_pages, maximum_retries=0),
        transport=transport, identity_registry=registry,
    )


def test_provider_is_disabled_by_default_and_makes_no_request():
    transport = FakeTransport({1: envelope([row()])})
    result = BybitAnnouncementsProvider(transport=transport).fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert result == ()
    assert transport.calls == []


def test_documented_envelope_maps_listing_and_e3_identity_without_title_matching():
    transport = FakeTransport({1: envelope([row(tag="ARB")])})
    result = provider(transport, registry=identity_registry()).fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert len(result) == 1
    item = result[0]
    assert item.event_type is CryptoCatalystType.EXCHANGE_LISTING
    assert item.provider_id == "BYBIT"
    assert item.provider_event_id.endswith("arb-1/")
    assert item.association_confidence is CryptoAssociationConfidence.PROVIDER_MAPPED
    assert item.project.project_id == "arbitrum"
    assert item.associated_pairs[0].pair.canonical_pair == "ARB/USD"
    assert item.expected_direction.value == "UNKNOWN"


def test_unknown_tag_stays_unresolved_and_webull_pair_is_not_required():
    transport = FakeTransport({1: envelope([row(tag="UNREGISTERED", title="New Listing: Unknown")])})
    item = provider(transport, registry=identity_registry()).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.association_confidence is CryptoAssociationConfidence.UNRESOLVED
    assert item.associated_pairs == ()
    assert item.project is None


def test_structured_categories_are_conservative():
    transport = FakeTransport({1: envelope([
        row(event="d", type_key="delistings", title="Asset maintenance", tag="ARB"),
        row(event="u", type_key="latest_activities", title="Activity", tag="ARB"),
        row(event="x", type_key="product_updates", title="Product update", tag="ARB"),
    ])})
    items = provider(transport, registry=identity_registry()).fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert [item.event_type for item in items] == [CryptoCatalystType.EXCHANGE_DELISTING, CryptoCatalystType.UNKNOWN, CryptoCatalystType.UNKNOWN]


def test_timestamps_are_separate_and_future_publication_is_excluded():
    future = row(publish=T0 + timedelta(minutes=1), effective=T0 + timedelta(hours=2))
    scheduled = row(event="scheduled", effective=T0 + timedelta(hours=2))
    transport = FakeTransport({1: envelope([future, scheduled])})
    items = provider(transport).fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert len(items) == 1
    assert items[0].published_at < items[0].observed_at
    assert items[0].effective_at == T0 + timedelta(hours=2)
    assert items[0].status.value == "SCHEDULED"


def test_pagination_and_duplicate_suppression_are_bounded():
    first = row(event="same")
    transport = FakeTransport({1: envelope([first], total=2), 2: envelope([first, row(event="two")], total=2)})
    provider_instance = provider(transport, maximum_pages=2)
    items = provider_instance.fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert len(items) == 2
    assert len(transport.calls) == 2
    assert provider_instance.metrics.bybit_duplicates_suppressed == 1
    assert provider_instance.metrics.bybit_pages_fetched == 2


def test_revision_update_changes_revision_without_changing_event_identity():
    updated = row(updated=T0 - timedelta(minutes=1))
    item = provider(FakeTransport({1: envelope([updated])})).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    assert item.revision > 1
    assert item.event_identity == item.event_identity


@pytest.mark.parametrize("kind", [BybitFailureKind.TIMEOUT, BybitFailureKind.RATE_LIMITED, BybitFailureKind.PROVIDER_5XX, BybitFailureKind.SCHEMA_ERROR])
def test_provider_failures_are_sanitized_and_categorized(kind):
    error = BybitProviderError(kind, "sanitized failure", status_code=429 if kind is BybitFailureKind.RATE_LIMITED else None)
    with pytest.raises(BybitProviderError) as exc:
        provider(FakeTransport(error=error)).fetch_page()
    assert exc.value.kind is kind
    assert "token" not in str(exc.value).lower()


def test_malformed_rows_are_contained():
    malformed = {"title": "bad", "type": {"key": "new_crypto"}}
    transport = FakeTransport({1: envelope([malformed, row(event="good")])})
    items = provider(transport).fetch_since(T0 - timedelta(days=1), observed_at=T0)
    assert len(items) == 1
    assert items[0].provider_event_id.endswith("good/")


def test_aggregator_receives_provider_neutral_evidence_and_deduplicates_polling():
    transport = FakeTransport({1: envelope([row()])})
    instance = provider(transport)
    first = instance.fetch_since(T0 - timedelta(days=1), observed_at=T0)
    second = instance.fetch_since(T0 - timedelta(days=1), observed_at=T0 + timedelta(minutes=1))
    aggregator = CryptoCatalystAggregator()
    aggregate = aggregator.aggregate((*first, *second), decision_cutoff=T0 + timedelta(minutes=1))
    assert len(aggregate.events) == 1
    assert aggregate.metrics.crypto_catalyst_duplicates_suppressed >= 1


def test_authority_flags_are_research_only():
    item = provider(FakeTransport({1: envelope([row()])})).fetch_since(T0 - timedelta(days=1), observed_at=T0)[0]
    payload = item.to_dict()
    assert payload["research_only"] is True
    assert payload["production_promoted"] is False
    assert payload["selection_authorized"] is False
    assert payload["execution_authorized"] is False

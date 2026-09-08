from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.crypto_research import (
    CryptoCatalystAggregator,
    OfficialStatusSource,
    StatusPageFailureKind,
    StatusPagePolicy,
    StatusPageProvider,
    StatusPageProviderError,
)


T0 = datetime(2026, 9, 8, 15, tzinfo=UTC)


SOURCE = OfficialStatusSource(
    "solana-status", "Solana Status", "https://status.solana.com", "ATLASSIAN_STATUSPAGE",
    "https://solana.com", ownership_verified=True, network_id="solana",
)


def component(component_id="cluster", status="operational"):
    return {"id": component_id, "name": "Mainnet Beta - Cluster", "status": status, "group_id": None, "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T00:00:00Z"}


def incident(incident_id="incident-1", *, status="resolved", impact="major", updates=None, created="2024-02-06T10:22:42Z", name="Cluster Instability"):
    updates = updates or [{"id": "update-1", "status": status, "body": "Network status update", "created_at": "2024-02-06T10:30:00Z", "updated_at": "2024-02-06T10:30:00Z", "affected_components": [{"code": "cluster", "new_status": "operational"}]}]
    return {"id": incident_id, "name": name, "status": status, "impact": impact, "created_at": created, "updated_at": "2024-02-06T15:09:24Z", "started_at": "2024-02-06T10:22:42Z", "monitoring_at": None, "resolved_at": "2024-02-06T15:09:24Z" if status == "resolved" else None, "shortlink": "https://status.solana.com/incidents/incident-1", "components": [{"id": "cluster", "name": "Mainnet Beta - Cluster", "status": "major_outage"}], "incident_updates": updates}


class FakeTransport:
    def __init__(self, payloads=None, error=None):
        self.payloads = payloads or {}
        self.error = error
        self.calls = []

    def get(self, url, *, timeout):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.payloads["components"] if url.endswith("components.json") else self.payloads["incidents"]


def provider(transport, *, enabled=True, maximum_updates=64):
    return StatusPageProvider(
        (SOURCE,), StatusPagePolicy(enabled=enabled, maximum_retries=0, maximum_updates_per_incident=maximum_updates), transport=transport,
    )


def payload(incidents):
    return {"components": {"page": {"id": "solana"}, "components": [component()]}, "incidents": {"page": {"id": "solana"}, "incidents": incidents}}


def test_disabled_by_default_makes_no_request():
    transport = FakeTransport(payload( [incident()]))
    assert provider(transport, enabled=False).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0) == ()
    assert transport.calls == []


def test_real_statuspage_schema_maps_incident_update_and_component():
    transport = FakeTransport(payload([incident(status="investigating", impact="major")]))
    items = provider(transport).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0)
    assert len(items) == 1
    item = items[0]
    assert item.provider_id == "STATUSPAGE"
    assert item.provider_event_id == "update-1"
    assert item.association_confidence.value == "PROVIDER_MAPPED"
    assert item.event_type.value == "NETWORK_OUTAGE"
    assert item.status.value == "ACTIVE"
    assert item.effective_at == datetime(2024, 2, 6, 10, 22, 42, tzinfo=UTC)


def test_resolved_update_maps_to_recovery():
    item = provider(FakeTransport(payload([incident(status="resolved", impact="critical")]))).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0)[0]
    assert item.event_type.value == "NETWORK_RECOVERY"
    assert item.status.value == "RESOLVED"


def test_multiple_updates_are_distinct_revisions():
    updates = [
        {"id": "u1", "status": "investigating", "body": "Investigating", "created_at": "2024-02-06T10:30:00Z", "updated_at": "2024-02-06T10:30:00Z", "affected_components": [{"code": "cluster"}]},
        {"id": "u2", "status": "resolved", "body": "Resolved", "created_at": "2024-02-06T15:09:24Z", "updated_at": "2024-02-06T15:09:24Z", "affected_components": [{"code": "cluster"}]},
    ]
    items = provider(FakeTransport(payload([incident(updates=updates)]))).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0)
    assert [item.provider_event_id for item in items] == ["u1", "u2"]
    assert {item.revision for item in items} == {2}


def test_point_in_time_cutoff_excludes_later_resolution():
    updates = [
        {"id": "u1", "status": "investigating", "body": "Investigating", "created_at": "2024-02-06T10:30:00Z", "updated_at": "2024-02-06T10:30:00Z", "affected_components": []},
        {"id": "u2", "status": "resolved", "body": "Resolved", "created_at": "2024-02-06T15:09:24Z", "updated_at": "2024-02-06T15:09:24Z", "affected_components": []},
    ]
    cutoff = datetime(2024, 2, 6, 12, tzinfo=UTC)
    items = provider(FakeTransport(payload([incident(updates=updates)]))).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=cutoff)
    assert [item.provider_event_id for item in items] == ["u1"]
    assert items[0].status.value == "ACTIVE"


def test_same_title_different_incidents_remain_distinct():
    items = provider(FakeTransport(payload([incident("a"), incident("b")]))).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0)
    assert {item.underlying_event_key for item in items} == {"solana-status:a", "solana-status:b"}


def test_unverified_source_is_rejected():
    with pytest.raises(ValueError):
        OfficialStatusSource("fake", "Fake", "https://status.example", "ATLASSIAN_STATUSPAGE", "https://example", ownership_verified=False)


@pytest.mark.parametrize("kind", list(StatusPageFailureKind))
def test_failures_are_contained(kind):
    error = StatusPageProviderError(kind, "sanitized status failure", status_code=429 if kind is StatusPageFailureKind.RATE_LIMITED else None)
    instance = provider(FakeTransport(error=error))
    assert instance.fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0) == ()
    assert instance.metrics.statuspage_requests_failed == 1


def test_aggregator_and_authority_flags():
    item = provider(FakeTransport(payload([incident(status="investigating")]))).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0)[0]
    aggregate = CryptoCatalystAggregator().aggregate((item, item), decision_cutoff=T0)
    assert len(aggregate.events) == 1
    data = item.to_dict()
    assert data["research_only"] is True
    assert data["selection_authorized"] is False
    assert data["execution_authorized"] is False


def test_component_only_scope_does_not_infer_security():
    item = provider(FakeTransport(payload([incident(status="investigating", impact="minor")]))).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0)[0]
    assert item.event_type.value == "NETWORK_CONGESTION"
    assert item.event_type.value not in {"SECURITY_EXPLOIT", "SECURITY_PATCH"}


def test_replay_is_deterministic():
    first = provider(FakeTransport(payload([incident()]))).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0)
    second = provider(FakeTransport(payload([incident()]))).fetch_since(datetime(2024, 1, 1, tzinfo=UTC), observed_at=T0)
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]

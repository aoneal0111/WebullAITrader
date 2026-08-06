from dataclasses import FrozenInstanceError

import pytest

from app.capabilities import (
    AssetCapability,
    CapabilityAvailability,
    CapabilityEntry,
    CapabilitySnapshot,
    SessionCapability,
)


def test_unknown_snapshot_covers_every_asset_and_session() -> None:
    snapshot = CapabilitySnapshot.unknown()

    assert tuple(entry.name for entry in snapshot.assets) == tuple(AssetCapability)
    assert tuple(entry.name for entry in snapshot.sessions) == tuple(
        SessionCapability
    )
    assert all(
        entry.availability is CapabilityAvailability.UNKNOWN
        for entry in (*snapshot.assets, *snapshot.sessions)
    )

    with pytest.raises(FrozenInstanceError):
        snapshot.assets = ()  # type: ignore[misc]


def test_snapshot_rejects_duplicate_or_misclassified_entries() -> None:
    stocks = CapabilityEntry(
        AssetCapability.STOCKS,
        CapabilityAvailability.AVAILABLE,
    )
    with pytest.raises(ValueError, match="unique"):
        CapabilitySnapshot(assets=(stocks, stocks), sessions=())
    with pytest.raises(TypeError, match="invalid"):
        CapabilitySnapshot(assets=(), sessions=(stocks,))


def test_lookup_returns_classified_state() -> None:
    snapshot = CapabilitySnapshot(
        assets=(CapabilityEntry(
            AssetCapability.STOCKS,
            CapabilityAvailability.AVAILABLE,
        ),),
        sessions=(CapabilityEntry(
            SessionCapability.OVERNIGHT,
            CapabilityAvailability.SUBSCRIPTION_REQUIRED,
        ),),
    )

    assert snapshot.availability_for(AssetCapability.STOCKS) is (
        CapabilityAvailability.AVAILABLE
    )
    assert snapshot.availability_for(SessionCapability.OVERNIGHT) is (
        CapabilityAvailability.SUBSCRIPTION_REQUIRED
    )

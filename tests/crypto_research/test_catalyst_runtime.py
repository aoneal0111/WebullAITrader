from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.crypto_research import (
    CryptoAssociationConfidence,
    CryptoAssociationType,
    CryptoCatalystAcquisitionRuntime,
    DEFAULT_INITIAL_LOOKBACK_SECONDS,
    FetchSinceCatalystProviderAdapter,
    adapt_catalyst_provider,
    set_catalyst_provider_enabled,
    CryptoCatalystDirection,
    CryptoCatalystEvidence,
    CryptoCatalystProviderAvailability,
    CryptoCatalystStatus,
    CryptoCatalystType,
    CryptoPairAssociation,
    CryptoProjectIdentity,
    bootstrap_pair_identity,
)


T0 = datetime(2026, 9, 8, 12, tzinfo=UTC)


def evidence(provider: str = "SEC_EDGAR", *, published: datetime = T0 - timedelta(hours=1)):
    project = CryptoProjectIdentity("solana", "Solana", "SOL")
    pair = bootstrap_pair_identity("SOL/USD", "SOLUSD", "sol")
    association = CryptoPairAssociation(pair, CryptoAssociationType.PROJECT, CryptoAssociationConfidence.EXACT, "fixture")
    return CryptoCatalystEvidence(
        CryptoCatalystType.PROTOCOL_UPGRADE, CryptoCatalystStatus.ANNOUNCED,
        provider, provider, provider + ":upgrade-1", published, published,
        project=project, token_symbol="SOL", associated_pairs=(association,),
        association_confidence=CryptoAssociationConfidence.EXACT,
        expected_direction=CryptoCatalystDirection.UNKNOWN,
        title="Upgrade", provider_event_id="upgrade-1", underlying_event_key="upgrade-1",
    )


class Provider:
    def __init__(self, provider_id="SEC_EDGAR", values=(), error=None):
        self.provider_id = provider_id
        self.values = tuple(values)
        self.error = error
        self.calls = 0

    def collect(self, _as_of):
        self.calls += 1
        if self.error:
            raise self.error
        return self.values


class FetchSinceProvider:
    def __init__(self, provider_id="FEDERAL_REGISTER", values=(), failures=0):
        self.provider_id = provider_id
        self.values = tuple(values)
        self.failures = failures
        self.calls = []

    def fetch_since(self, since, *, observed_at):
        self.calls.append((since, observed_at))
        if self.failures:
            self.failures -= 1
            raise RuntimeError("fixture failure")
        return self.values


def test_disabled_runtime_never_calls_providers():
    provider = Provider(values=(evidence(),))
    runtime = CryptoCatalystAcquisitionRuntime(enabled=False, providers=(provider,))
    assert runtime.start() is False
    assert runtime.run_once(now=T0, force=True) == 0
    assert provider.calls == 0
    assert runtime.close()


def test_provider_failure_isolated_and_blocked_state_is_explicit():
    good = Provider("SEC_EDGAR", (evidence(),))
    blocked = Provider("BYBIT", error=RuntimeError("CloudFront geographic block"))
    runtime = CryptoCatalystAcquisitionRuntime(enabled=True, providers=(good, blocked))
    runtime._accepting = True
    runtime.run_once(now=T0, force=True)
    assert good.calls == 1
    assert runtime.metrics().requests_failed == 1
    snapshot = runtime.snapshot_at(bootstrap_pair_identity("SOL/USD", "SOLUSD"), T0)
    states = {item.provider_id: item.availability_state for item in snapshot.provider_availability}
    assert states["BYBIT"] is CryptoCatalystProviderAvailability.BLOCKED
    assert states["SEC_EDGAR"] is CryptoCatalystProviderAvailability.AVAILABLE
    runtime.close()


def test_cutoff_view_excludes_later_evidence_and_repeated_polling_deduplicates():
    first = evidence(published=T0 - timedelta(minutes=10))
    later = evidence(published=T0 + timedelta(minutes=10))
    provider = Provider(values=(first,))
    runtime = CryptoCatalystAcquisitionRuntime(enabled=True, providers=(provider,))
    runtime._accepting = True
    runtime.run_once(now=T0, force=True)
    provider.values = (first, later)
    runtime.run_once(now=T0 + timedelta(minutes=20), force=True)
    snapshot = runtime.snapshot_at(bootstrap_pair_identity("SOL/USD", "SOLUSD"), T0)
    assert snapshot.retained_event_count == 1
    assert snapshot.events[0].published_at == first.published_at
    assert runtime.metrics().duplicate_evidence >= 1
    runtime.close()


def test_fetch_since_adapter_uses_bounded_initial_lookback_and_watermark():
    provider = FetchSinceProvider(values=(evidence(provider="FEDERAL_REGISTER"),))
    adapter = FetchSinceCatalystProviderAdapter(provider)
    first = T0
    second = T0 + timedelta(hours=2)
    assert tuple(adapter.collect(first))
    assert provider.calls[0][0] == first - timedelta(seconds=DEFAULT_INITIAL_LOOKBACK_SECONDS["FEDERAL_REGISTER"])
    assert provider.calls[0][1] == first
    assert adapter.watermark == first
    tuple(adapter.collect(second))
    assert provider.calls[1] == (first, second)
    assert adapter.watermark == second


def test_fetch_since_failure_does_not_advance_watermark_and_overlap_deduplicates():
    provider = FetchSinceProvider(values=(evidence(provider="FEDERAL_REGISTER"),), failures=1)
    adapter = FetchSinceCatalystProviderAdapter(provider)
    runtime = CryptoCatalystAcquisitionRuntime(enabled=True, providers=(adapter,))
    runtime._accepting = True
    runtime.run_once(now=T0, force=True)
    assert runtime.metrics().requests_failed == 1
    assert adapter.watermark is None
    runtime.run_once(now=T0 + timedelta(minutes=5), force=True)
    assert adapter.watermark == T0 + timedelta(minutes=5)
    runtime.run_once(now=T0 + timedelta(minutes=10), force=True)
    assert runtime.metrics().duplicate_evidence >= 1
    runtime.close()


def test_existing_collect_provider_is_not_wrapped():
    provider = Provider("BYBIT")
    assert adapt_catalyst_provider(provider) is provider


def test_provider_policy_enablement_is_minimal_and_explicit():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Policy:
        enabled: bool = False

    class PolicyProvider:
        provider_id = "FEDERAL_REGISTER"
        policy = Policy()

        def fetch_since(self, _since, *, observed_at):
            return ()

    provider = PolicyProvider()
    set_catalyst_provider_enabled(provider, True)
    assert provider.policy.enabled is True
    assert isinstance(adapt_catalyst_provider(provider), FetchSinceCatalystProviderAdapter)

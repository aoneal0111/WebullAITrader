from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.crypto_research import (
    CryptoCatalystAcquisitionRuntime,
    CryptoCatalystCollectionConfig,
    CryptoCatalystCollectionSink,
    CryptoIntelligenceResearchRuntime,
    CryptoPair,
    CryptoResearchRuntime,
    WebullCryptoResearchProvider,
    adapt_catalyst_provider,
    set_catalyst_provider_enabled,
)
from app.webull.client_factories import MarketDataClientFactory
from app.webull.request_audit import AuditedMarketDataClient, RequestIsolationGuard
from app.webull.sdk_market_data import LazyOfficialDataClient


@dataclass(slots=True)
class OptionalResearchRuntimes:
    """Non-authoritative research runtimes composed outside the trading core."""

    crypto_research_runtime: CryptoResearchRuntime
    crypto_catalyst_acquisition_runtime: CryptoCatalystAcquisitionRuntime | None
    crypto_intelligence_runtime: CryptoIntelligenceResearchRuntime | None

    def start(self) -> None:
        if self.crypto_catalyst_acquisition_runtime is not None:
            self.crypto_catalyst_acquisition_runtime.start()
        if (
            self.crypto_intelligence_runtime is not None
            and self.crypto_intelligence_runtime.enabled
        ):
            self.crypto_intelligence_runtime.start()
        if self.crypto_research_runtime.enabled:
            self.crypto_research_runtime.start()

    def close(self) -> bool:
        # Stop the producer before its consumers; keep failed workers reserved.
        results = [self.crypto_research_runtime.close()]
        for runtime in (self.crypto_catalyst_acquisition_runtime, self.crypto_intelligence_runtime):
            if runtime is not None:
                results.append(runtime.close())
        return all(results)


def create_optional_research_runtimes(
    *,
    operational_configuration: object,
    chart_market_configuration: object,
    chart_request_guard: RequestIsolationGuard,
    catalyst_providers: Sequence[object] = (),
) -> OptionalResearchRuntimes:
    """Construct optional research services without granting execution authority."""

    crypto_data_client = LazyOfficialDataClient(
        lambda: AuditedMarketDataClient(
            MarketDataClientFactory(chart_market_configuration).create(
                timeout_seconds=5.0
            ),
            chart_request_guard,
            chart_market_configuration,
        )
    )
    crypto_research_runtime = CryptoResearchRuntime(
        enabled=operational_configuration.crypto_discovery_enabled,
        provider=WebullCryptoResearchProvider(crypto_data_client),
        configured_pairs=tuple(
            CryptoPair.configured(value)
            for value in operational_configuration.crypto_discovery_symbols
        ),
        path=operational_configuration.crypto_discovery_path,
        queue_capacity=operational_configuration.crypto_discovery_queue_capacity,
        refresh_seconds=operational_configuration.crypto_discovery_refresh_seconds,
        intelligence_history_max_symbols=(
            operational_configuration.crypto_intelligence_history_max_symbols
        ),
        intelligence_history_bar_count=(
            operational_configuration.crypto_intelligence_history_bar_count
        ),
        intelligence_history_request_budget=(
            operational_configuration.crypto_intelligence_history_request_budget
        ),
        intelligence_m1_refresh_seconds=(
            operational_configuration.crypto_intelligence_m1_refresh_seconds
        ),
        intelligence_m5_refresh_seconds=(
            operational_configuration.crypto_intelligence_m5_refresh_seconds
        ),
    )

    crypto_catalyst_acquisition_runtime = None
    if operational_configuration.crypto_catalyst_acquisition_enabled:
        provider_flags = {
            "SEC_EDGAR": operational_configuration.crypto_catalyst_sec_enabled,
            "FEDERAL_REGISTER": (
                operational_configuration.crypto_catalyst_federal_register_enabled
            ),
            "STATUSPAGE": operational_configuration.crypto_catalyst_statuspage_enabled,
            "BYBIT": operational_configuration.crypto_catalyst_bybit_enabled,
        }
        composed_catalyst_providers = tuple(
            adapt_catalyst_provider(
                set_catalyst_provider_enabled(
                    provider,
                    provider_flags.get(
                        str(getattr(provider, "provider_id", "")),
                        False,
                    ),
                )
            )
            for provider in catalyst_providers
        )
        crypto_catalyst_acquisition_runtime = CryptoCatalystAcquisitionRuntime(
            enabled=True,
            providers=composed_catalyst_providers,
            cadences={
                "SEC_EDGAR": (
                    operational_configuration.crypto_catalyst_sec_cadence_seconds
                ),
                "FEDERAL_REGISTER": (
                    operational_configuration
                    .crypto_catalyst_federal_register_cadence_seconds
                ),
                "STATUSPAGE": (
                    operational_configuration.crypto_catalyst_statuspage_cadence_seconds
                ),
                "BYBIT": (
                    operational_configuration.crypto_catalyst_bybit_cadence_seconds
                ),
            },
            provider_enabled=provider_flags,
            scheduler_tick_seconds=(
                operational_configuration.crypto_catalyst_scheduler_tick_seconds
            ),
        )

    crypto_intelligence_runtime = None
    if operational_configuration.crypto_intelligence_enabled:
        collection_config = CryptoCatalystCollectionConfig.from_environment()
        collection_sink = (
            CryptoCatalystCollectionSink(collection_config)
            if collection_config.enabled
            else None
        )
        crypto_intelligence_runtime = CryptoIntelligenceResearchRuntime(
            enabled=True,
            catalyst_view=crypto_catalyst_acquisition_runtime,
            collection=collection_sink,
            maximum_active_decisions=(
                operational_configuration.crypto_intelligence_max_active_decisions
            ),
        )
        crypto_research_runtime.configure_intelligence(
            context_sink=crypto_intelligence_runtime.submit_context,
            outcome_sink=crypto_intelligence_runtime.update_outcomes,
        )

    return OptionalResearchRuntimes(
        crypto_research_runtime=crypto_research_runtime,
        crypto_catalyst_acquisition_runtime=crypto_catalyst_acquisition_runtime,
        crypto_intelligence_runtime=crypto_intelligence_runtime,
    )


__all__ = [
    "OptionalResearchRuntimes",
    "create_optional_research_runtimes",
]

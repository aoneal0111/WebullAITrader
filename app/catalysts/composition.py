from __future__ import annotations

from app.catalysts.provider import CatalystProvider
from app.catalysts.cnbc import (
    CNBCCatalystProvider,
    CNBCNewsPolicy,
    log_cnbc_provider_state,
)
from app.catalysts.sec_edgar import (
    SECEdgarCatalystProvider,
    SECEdgarPolicy,
    log_sec_edgar_provider_state,
)
from app.catalysts.webull import WebullCatalystProvider
from app.catalysts.yahoo_finance import (
    YahooFinanceCatalystProvider,
    YahooFinanceNewsPolicy,
    log_yahoo_finance_provider_state,
)
from app.configuration import OperationalConfiguration


def build_catalyst_providers(
    webull_client: object,
    configuration: OperationalConfiguration,
) -> tuple[CatalystProvider, ...]:
    """Build the shared desktop/forward-live evidence provider set."""

    providers: list[CatalystProvider] = [WebullCatalystProvider(webull_client)]
    if configuration.sec_edgar is None:
        log_sec_edgar_provider_state(enabled=False)
    else:
        providers.append(
            SECEdgarCatalystProvider(
                SECEdgarPolicy(
                    user_agent=configuration.sec_edgar.user_agent,
                    freshness_days=configuration.sec_edgar.freshness_days,
                    timeout_seconds=configuration.sec_edgar.timeout_seconds,
                )
            )
        )
    if configuration.yahoo_finance_news is None:
        log_yahoo_finance_provider_state(enabled=False)
    else:
        yahoo = configuration.yahoo_finance_news
        providers.append(
            YahooFinanceCatalystProvider(
                YahooFinanceNewsPolicy(
                    freshness_minutes=yahoo.freshness_minutes,
                    timeout_seconds=yahoo.timeout_seconds,
                    cache_ttl_seconds=yahoo.cache_ttl_seconds,
                )
            )
        )
    if configuration.cnbc_news is None:
        log_cnbc_provider_state(enabled=False)
    else:
        cnbc = configuration.cnbc_news
        providers.append(
            CNBCCatalystProvider(
                CNBCNewsPolicy(
                    freshness_minutes=cnbc.freshness_minutes,
                    timeout_seconds=cnbc.timeout_seconds,
                    refresh_ttl_seconds=cnbc.refresh_ttl_seconds,
                    failure_cooldown_seconds=cnbc.failure_cooldown_seconds,
                    maximum_snapshot_age_seconds=cnbc.maximum_snapshot_age_seconds,
                    max_items=cnbc.max_items,
                    max_payload_bytes=cnbc.max_payload_bytes,
                )
            )
        )
    return tuple(providers)


__all__ = ["build_catalyst_providers"]

from __future__ import annotations

from app.momentum_scanner.models import AssetClass
from app.reference_data.cache import ReferenceDataCache
from app.reference_data.models import (
    ReferenceDataPolicy,
    ReferenceRecord,
)
from app.reference_data.provider import ReferenceDataProvider


class ReferenceDataService:
    def __init__(
        self,
        provider: ReferenceDataProvider,
        *,
        cache: ReferenceDataCache | None = None,
        policy: ReferenceDataPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._cache = cache if cache is not None else ReferenceDataCache()
        self._policy = policy if policy is not None else ReferenceDataPolicy()

    def get(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.STOCK,
        *,
        force_refresh: bool = False,
    ) -> ReferenceRecord:
        normalized_symbol = _normalize_symbol(symbol)

        if not force_refresh:
            cached = self._cache.get(
                normalized_symbol,
                asset_class,
            )

            if cached is not None:
                return cached

        record = self._provider.get_reference_data(
            normalized_symbol,
            asset_class,
        )

        self._validate_provider_response(
            requested_symbol=normalized_symbol,
            requested_asset_class=asset_class,
            record=record,
        )

        self._cache.put(
            record,
            ttl=self._policy.ttl_for(asset_class),
        )

        return record

    def refresh(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.STOCK,
    ) -> ReferenceRecord:
        return self.get(
            symbol,
            asset_class,
            force_refresh=True,
        )

    def invalidate(
        self,
        symbol: str,
        asset_class: AssetClass = AssetClass.STOCK,
    ) -> None:
        self._cache.invalidate(symbol, asset_class)

    def clear_cache(self) -> None:
        self._cache.clear()

    @staticmethod
    def _validate_provider_response(
        *,
        requested_symbol: str,
        requested_asset_class: AssetClass,
        record: ReferenceRecord,
    ) -> None:
        if record.symbol != requested_symbol:
            raise ValueError(
                "provider returned a different symbol: "
                f"requested={requested_symbol}, "
                f"returned={record.symbol}"
            )

        if record.asset_class is not requested_asset_class:
            raise ValueError(
                "provider returned a different asset class: "
                f"requested={requested_asset_class.value}, "
                f"returned={record.asset_class.value}"
            )


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()

    if not normalized:
        raise ValueError("symbol is required")

    return normalized




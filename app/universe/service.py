from __future__ import annotations

from collections.abc import Iterable

from app.momentum_scanner.models import AssetClass
from app.universe.filters import (
    UniverseFilterConfig,
    is_eligible,
)
from app.universe.models import (
    UniverseSelection,
    UniverseSymbol,
)
from app.universe.provider import UniverseProvider


class UniverseService:
    def __init__(
        self,
        provider: UniverseProvider,
        *,
        config: UniverseFilterConfig | None = None,
    ) -> None:
        self._provider = provider
        self._config = (
            config
            if config is not None
            else UniverseFilterConfig()
        )

    def select(
        self,
        asset_class: AssetClass,
    ) -> UniverseSelection:
        candidates = self._provider.list_symbols(
            asset_class
        )

        included: list[UniverseSymbol] = []
        excluded: list[UniverseSymbol] = []

        for item in candidates:
            target = (
                included
                if is_eligible(item, self._config)
                else excluded
            )
            target.append(item)

        return UniverseSelection(
            included=tuple(included),
            excluded=tuple(excluded),
        )

    def select_all(
        self,
        asset_classes: Iterable[AssetClass] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
    ) -> UniverseSelection:
        included: dict[
            tuple[AssetClass, str],
            UniverseSymbol,
        ] = {}
        excluded: dict[
            tuple[AssetClass, str],
            UniverseSymbol,
        ] = {}

        for asset_class in asset_classes:
            selection = self.select(asset_class)

            for item in selection.included:
                key = (item.asset_class, item.symbol)
                included[key] = item
                excluded.pop(key, None)

            for item in selection.excluded:
                key = (item.asset_class, item.symbol)

                if key not in included:
                    excluded[key] = item

        sort_key = lambda item: (
            item.asset_class.value,
            item.symbol,
        )

        return UniverseSelection(
            included=tuple(
                sorted(included.values(), key=sort_key)
            ),
            excluded=tuple(
                sorted(excluded.values(), key=sort_key)
            ),
        )

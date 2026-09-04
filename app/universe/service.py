from __future__ import annotations

from collections.abc import Iterable

from app.momentum_scanner.models import AssetClass
from app.universe.filters import (
    UniverseFilterConfig,
    exclusion_reasons,
    is_eligible,
)
from app.scanner_universe_observability import (
    UniverseAdmissionOutcome,
    UniverseAdmissionStage,
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
        admission_observer: object | None = None,
    ) -> None:
        self._provider = provider
        self._config = (
            config
            if config is not None
            else UniverseFilterConfig()
        )
        self._admission_observer = admission_observer

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
            eligible = is_eligible(item, self._config)
            reasons = exclusion_reasons(item, self._config)
            _observe_admission(
                self._admission_observer,
                stage=(
                    UniverseAdmissionStage.UNIVERSE_FILTER_ACCEPTED
                    if eligible
                    else UniverseAdmissionStage.UNIVERSE_FILTER_REJECTED
                ),
                outcome=(
                    UniverseAdmissionOutcome.ACCEPTED
                    if eligible
                    else UniverseAdmissionOutcome.REJECTED
                ),
                reason=(
                    "ELIGIBLE_EXISTING_UNIVERSE_FILTER"
                    if eligible
                    else "|".join(reason.upper() for reason in reasons)
                    or "INELIGIBLE_EXISTING_UNIVERSE_FILTER"
                ),
                raw_symbol=item.display_symbol,
                normalized_symbol=item.symbol,
                upstream_fields={
                    "asset_class": item.asset_class.value,
                    "api_symbol": item.api_symbol,
                },
            )
            target = included if eligible else excluded
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


def _observe_admission(observer: object | None, **values) -> None:
    callback = getattr(observer, "record", None)
    if not callable(callback):
        return
    try:
        callback(**values)
    except Exception:
        pass

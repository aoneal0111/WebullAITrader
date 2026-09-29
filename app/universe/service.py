from __future__ import annotations

from time import perf_counter

from collections.abc import Iterable

from app.performance_diagnostics import performance_diagnostics
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
    UniversePriorityLanes,
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
        ordering_source: object | None = None,
    ) -> None:
        self._provider = provider
        self._config = (
            config
            if config is not None
            else UniverseFilterConfig()
        )
        self._admission_observer = admission_observer
        self._ordering_source = ordering_source

    def select(
        self,
        asset_class: AssetClass,
    ) -> UniverseSelection:
        return self._select_candidates(
            self._provider.list_symbols(asset_class)
        )

    def select_startup(
        self,
        asset_class: AssetClass,
    ) -> UniverseSelection:
        startup = getattr(self._provider, "list_startup_symbols", None)
        candidates = (
            startup(asset_class)
            if callable(startup)
            else self._provider.list_symbols(asset_class)
        )
        return self._select_candidates(candidates)

    def _select_candidates(
        self,
        candidates: Iterable[UniverseSymbol],
    ) -> UniverseSelection:

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
        return self._select_all_with(asset_classes, self.select)

    def select_startup_all(
        self,
        asset_classes: Iterable[AssetClass] = (
            AssetClass.STOCK,
            AssetClass.CRYPTO,
        ),
    ) -> UniverseSelection:
        return self._select_all_with(asset_classes, self.select_startup)

    def _select_all_with(
        self,
        asset_classes: Iterable[AssetClass],
        selector,
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
            stage_started = perf_counter()
            selection_success = False
            try:
                selection = selector(asset_class)
                selection_success = True
            finally:
                performance_diagnostics.record_component_duration(
                    f"scanner_start.universe_select.{asset_class.value}",
                    max(0.0, (perf_counter() - stage_started) * 1000.0),
                    success=selection_success,
                )

            for item in selection.included:
                key = (item.asset_class, item.symbol)
                included[key] = item
                excluded.pop(key, None)

            for item in selection.excluded:
                key = (item.asset_class, item.symbol)

                if key not in included:
                    excluded[key] = item

        priority = tuple()
        if self._ordering_source is not None:
            stage_started = perf_counter()
            priority_success = False
            try:
                priority = tuple(self._ordering_source.priority_order())
                priority_success = True
            except Exception:
                priority = tuple()
            finally:
                performance_diagnostics.record_component_duration(
                    "scanner_start.universe_priority_order",
                    max(0.0, (perf_counter() - stage_started) * 1000.0),
                    success=priority_success,
                )
        priority_index = {symbol: index for index, symbol in enumerate(priority)}
        sort_key = lambda item: (
            0 if item.symbol in priority_index else 1,
            priority_index.get(item.symbol, 0),
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

    def priority_lanes(self) -> UniversePriorityLanes:
        source = self._ordering_source or self._provider
        getter = getattr(source, "priority_lanes", None)
        if not callable(getter):
            return UniversePriorityLanes()
        try:
            value = getter()
        except Exception:
            return UniversePriorityLanes()
        return (
            value
            if isinstance(value, UniversePriorityLanes)
            else UniversePriorityLanes()
        )

    def set_accelerator_symbols_source(self, source) -> None:
        target = self._ordering_source or self._provider
        setter = getattr(target, "set_accelerator_symbols_source", None)
        if callable(setter):
            setter(source)


def _observe_admission(observer: object | None, **values) -> None:
    callback = getattr(observer, "record", None)
    if not callable(callback):
        return
    try:
        callback(**values)
    except Exception:
        pass

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.composition.exceptions import MissingDependencyError


@dataclass(frozen=True, slots=True)
class CompositionContainer:
    _components: Mapping[str, object]
    _order: tuple[str, ...]

    def __post_init__(self):
        values = dict(self._components)
        if tuple(values) != self._order:
            raise ValueError("component order must match registered components")
        object.__setattr__(self, "_components", MappingProxyType(values))

    def resolve(self, component_name: str):
        try:
            return self._components[component_name]
        except (KeyError, TypeError) as exc:
            raise MissingDependencyError(f"component is not registered: {component_name}") from exc

    def contains(self, component_name: str) -> bool:
        return component_name in self._components

    def list_components(self) -> tuple[str, ...]:
        return self._order

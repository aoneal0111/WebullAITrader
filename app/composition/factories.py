from dataclasses import dataclass
from typing import Callable, Mapping

from app.composition.exceptions import FactoryValidationError


@dataclass(frozen=True, slots=True)
class ComponentFactory:
    builder: Callable[..., object]
    dependencies: tuple[str, ...] = ()
    validator: Callable[[object], bool] | None = None

    def __post_init__(self):
        if not callable(self.builder):
            raise FactoryValidationError("factory builder must be callable")
        if not isinstance(self.dependencies, tuple):
            raise FactoryValidationError("factory dependencies must be a tuple")
        if any(not isinstance(name, str) or not name.strip() for name in self.dependencies):
            raise FactoryValidationError("dependency names must be non-empty strings")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise FactoryValidationError("factory dependencies must be unique")
        if self.validator is not None and not callable(self.validator):
            raise FactoryValidationError("factory validator must be callable")

    def create(self, resolved: Mapping[str, object]):
        try:
            component = self.builder(*(resolved[name] for name in self.dependencies))
        except FactoryValidationError:
            raise
        except Exception as exc:
            raise FactoryValidationError("component factory failed") from exc
        if component is None:
            raise FactoryValidationError("component factory returned None")
        if self.validator is not None:
            try:
                valid = self.validator(component)
            except Exception as exc:
                raise FactoryValidationError("component interface validation failed") from exc
            if valid is not True:
                raise FactoryValidationError("component has an incompatible interface")
        return component


def factory(builder, dependencies=(), validator=None):
    """Create an immutable constructor-injection factory declaration."""
    return ComponentFactory(builder, tuple(dependencies), validator)

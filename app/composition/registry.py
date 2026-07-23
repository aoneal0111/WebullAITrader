from collections import OrderedDict

from app.composition.container import CompositionContainer
from app.composition.exceptions import DuplicateRegistrationError, FactoryValidationError, MissingDependencyError
from app.composition.factories import ComponentFactory
from app.composition.policies import CompositionPolicy
from app.composition.validation import validate_factory_graph


class Registry:
    def __init__(self, policy: CompositionPolicy | None = None):
        self.policy = policy or CompositionPolicy()
        if not isinstance(self.policy, CompositionPolicy):
            raise FactoryValidationError("policy must be CompositionPolicy")
        self._factories = OrderedDict()

    def register(self, component_name: str, component_factory: ComponentFactory):
        if not isinstance(component_name, str) or not component_name.strip():
            raise FactoryValidationError("component name must be non-empty")
        if not isinstance(component_factory, ComponentFactory):
            raise FactoryValidationError("factory must be ComponentFactory")
        if component_name in self._factories and not self.policy.allow_overrides:
            raise DuplicateRegistrationError(f"component already registered: {component_name}")
        self._factories[component_name] = component_factory
        return self

    def contains(self, component_name: str) -> bool:
        return component_name in self._factories

    def list_components(self) -> tuple[str, ...]:
        return tuple(self._factories)

    def resolve(self, component_name: str):
        if component_name not in self._factories:
            raise MissingDependencyError(f"component is not registered: {component_name}")
        if self.policy.strict_validation:
            validate_factory_graph(self._factories)
        resolved = OrderedDict()
        active = []

        def construct(name):
            if name in resolved:
                return resolved[name]
            if name in active:
                from app.composition.exceptions import CircularDependencyError
                raise CircularDependencyError("circular dependency: " + " -> ".join(active + [name]))
            if name not in self._factories:
                owner = active[-1] if active else component_name
                raise MissingDependencyError(f"{owner} requires missing component: {name}")
            active.append(name)
            declaration = self._factories[name]
            dependencies = OrderedDict((dep, construct(dep)) for dep in declaration.dependencies)
            active.pop()
            resolved[name] = declaration.create(dependencies)
            return resolved[name]

        return construct(component_name)

    def build(self) -> CompositionContainer:
        if self.policy.strict_validation:
            validate_factory_graph(self._factories)
        resolved = OrderedDict()
        for name in self._factories:
            if name not in resolved:
                def add_graph(current):
                    if current in resolved:
                        return resolved[current]
                    declaration = self._factories[current]
                    dependencies = OrderedDict((dep, add_graph(dep)) for dep in declaration.dependencies)
                    resolved[current] = declaration.create(dependencies)
                    return resolved[current]
                add_graph(name)
        ordered = OrderedDict((name, resolved[name]) for name in self._factories)
        return CompositionContainer(ordered, tuple(ordered))


class CompositionRoot:
    def __init__(self, policy: CompositionPolicy | None = None):
        self.registry = Registry(policy)

    def register(self, component_name, component_factory):
        self.registry.register(component_name, component_factory)
        return self

    def build(self):
        return self.registry.build()

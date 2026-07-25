"""
Application composition root.

The composition package is responsible for constructing and wiring the
application dependency graph. Business logic belongs elsewhere.
"""

from .container import CompositionContainer
from .desktop import DesktopComposition, create_desktop_composition
from .exceptions import (
    CircularDependencyError,
    CompositionError,
    DuplicateRegistrationError,
    FactoryValidationError,
    MissingDependencyError,
)
from .factories import ComponentFactory, factory
from .operational_runtime import (
    OperationalRuntimeComposition,
    create_operational_runtime_composition,
)
from .policies import CompositionPolicy
from .registry import CompositionRoot, Registry
from .validation import implements_methods, validate_factory_graph

__all__ = [
    # Desktop composition
    "DesktopComposition",
    "create_desktop_composition",

    # Operational composition
    "OperationalRuntimeComposition",
    "create_operational_runtime_composition",

    # Existing composition framework
    "CompositionContainer",
    "CompositionRoot",
    "Registry",
    "CompositionPolicy",
    "ComponentFactory",
    "factory",
    "implements_methods",
    "validate_factory_graph",

    # Exceptions
    "CompositionError",
    "DuplicateRegistrationError",
    "MissingDependencyError",
    "CircularDependencyError",
    "FactoryValidationError",
]


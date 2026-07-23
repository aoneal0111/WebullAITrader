import pytest

from app.composition import (
    CompositionPolicy, DuplicateRegistrationError, FactoryValidationError,
    MissingDependencyError, Registry, factory,
)


def test_registry_order_contains_and_resolution():
    registry = Registry().register("first", factory(lambda: object()))
    registry.register("second", factory(lambda first: {"dependency": first}, ("first",)))
    assert registry.list_components() == ("first", "second")
    assert registry.contains("first")
    assert registry.resolve("second")["dependency"] is not None


def test_duplicate_registration_is_rejected():
    registry = Registry().register("item", factory(object))
    with pytest.raises(DuplicateRegistrationError):
        registry.register("item", factory(dict))


def test_explicit_override_preserves_registration_position():
    registry = Registry(CompositionPolicy(allow_overrides=True))
    registry.register("item", factory(lambda: 1)).register("item", factory(lambda: 2))
    assert registry.list_components() == ("item",)
    assert registry.resolve("item") == 2


def test_missing_component_and_invalid_factory_are_descriptive():
    registry = Registry()
    with pytest.raises(MissingDependencyError):
        registry.resolve("absent")
    with pytest.raises(FactoryValidationError):
        registry.register("bad", lambda: object())

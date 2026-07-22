import pytest

from app.composition import ComponentFactory, FactoryValidationError, factory


def test_factory_uses_constructor_injection_in_declared_order():
    declaration = factory(lambda left, right: (left, right), ("left", "right"))
    assert declaration.create({"left": 1, "right": 2}) == (1, 2)


def test_factory_validates_output_interface():
    declaration = factory(lambda: object(), validator=lambda value: hasattr(value, "execute"))
    with pytest.raises(FactoryValidationError, match="incompatible interface"):
        declaration.create({})


def test_factory_rejects_invalid_declaration_and_output():
    with pytest.raises(FactoryValidationError):
        ComponentFactory(None)
    with pytest.raises(FactoryValidationError, match="returned None"):
        factory(lambda: None).create({})

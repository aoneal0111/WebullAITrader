from dataclasses import FrozenInstanceError
import pytest

from app.composition import CompositionRoot, MissingDependencyError, factory


def test_container_is_frozen_ordered_and_immutable():
    root = CompositionRoot().register("a", factory(lambda: object()))
    root.register("b", factory(lambda a: (a,), ("a",)))
    container = root.build()
    assert container.list_components() == ("a", "b")
    assert container.resolve("b")[0] is container.resolve("a")
    assert container.contains("a")
    assert not hasattr(container, "__dict__")
    with pytest.raises(TypeError):
        container._components["c"] = object()
    with pytest.raises(FrozenInstanceError):
        container._order = ()
    with pytest.raises(MissingDependencyError):
        container.resolve("missing")


def test_each_build_has_no_hidden_singleton_state():
    root = CompositionRoot().register("value", factory(object))
    assert root.build().resolve("value") is not root.build().resolve("value")

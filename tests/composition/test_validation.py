import pytest

from app.composition import CircularDependencyError, MissingDependencyError, Registry, factory


def test_missing_dependency_detected_before_construction():
    registry = Registry().register("root", factory(lambda value: value, ("missing",)))
    with pytest.raises(MissingDependencyError, match="root requires missing component"):
        registry.build()


def test_cycle_reports_stable_path():
    registry = Registry()
    registry.register("a", factory(lambda b: b, ("b",)))
    registry.register("b", factory(lambda c: c, ("c",)))
    registry.register("c", factory(lambda a: a, ("a",)))
    with pytest.raises(CircularDependencyError, match="a -> b -> c -> a"):
        registry.build()

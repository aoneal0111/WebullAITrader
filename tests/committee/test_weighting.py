from __future__ import annotations

from types import MappingProxyType

import pytest

from app.committee import AgentWeightConfiguration


def test_default_configuration_contains_technical_agent() -> None:
    configuration = AgentWeightConfiguration()
    assert configuration.weight_for("technical_agent_v1") == 1.0
    assert configuration.weight_for("future_agent_v1") == 1.0


def test_zero_weight_is_allowed() -> None:
    configuration = AgentWeightConfiguration(weights={"disabled": 0})
    assert configuration.weight_for("disabled") == 0


@pytest.mark.parametrize(
    "weight",
    [-0.01, 1.01, float("nan"), float("inf"), True],
)
def test_invalid_weights_are_rejected(weight: object) -> None:
    with pytest.raises(ValueError):
        AgentWeightConfiguration(weights={"agent": weight})  # type: ignore[dict-item]


def test_empty_version_and_agent_name_are_rejected() -> None:
    with pytest.raises(ValueError, match="version"):
        AgentWeightConfiguration(version=" ")
    with pytest.raises(ValueError, match="agent names"):
        AgentWeightConfiguration(weights={" ": 1})


def test_weight_mapping_is_defensively_immutable() -> None:
    weights = {"agent": 0.5}
    configuration = AgentWeightConfiguration(weights=weights)
    weights["agent"] = 1
    assert isinstance(configuration.weights, MappingProxyType)
    assert configuration.weight_for("agent") == 0.5
    with pytest.raises(TypeError):
        configuration.weights["agent"] = 1  # type: ignore[index]


@pytest.mark.parametrize("minimum", [0, 1])
def test_minimum_confidence_accepts_boundaries(minimum: float) -> None:
    assert AgentWeightConfiguration(
        minimum_confidence=minimum
    ).minimum_confidence == minimum


@pytest.mark.parametrize("minimum", [-0.01, 1.01, True])
def test_invalid_minimum_confidence_is_rejected(minimum: object) -> None:
    with pytest.raises(ValueError, match="minimum_confidence"):
        AgentWeightConfiguration(minimum_confidence=minimum)  # type: ignore[arg-type]

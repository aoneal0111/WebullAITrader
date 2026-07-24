from __future__ import annotations

from typing import Protocol


class RuntimeDriverLike(Protocol):
    @property
    def environment(self) -> str: ...

    @property
    def active_model(self) -> str: ...


def validate_runtime_driver(driver: RuntimeDriverLike) -> None:
    """Validate the runtime driver contract."""

    if not isinstance(driver.environment, str):
        raise TypeError("runtime driver environment must be a string")

    if not driver.environment.strip():
        raise ValueError("runtime driver environment must not be empty")

    if not isinstance(driver.active_model, str):
        raise TypeError("runtime driver active_model must be a string")

    if not driver.active_model.strip():
        raise ValueError("runtime driver active_model must not be empty")

from dataclasses import dataclass
from typing import Any

from app.execution_coordinator.context_provider import CoordinationContext


@dataclass(frozen=True)
class RuntimeContextAssembler:
    """Assembles a coordination context from authoritative runtime inputs."""

    def build(self, *args: Any, **kwargs: Any) -> CoordinationContext:
        raise NotImplementedError(
            "Runtime context assembly is not implemented yet."
        )

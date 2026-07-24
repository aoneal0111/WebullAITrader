from dataclasses import dataclass
from typing import Any, Callable

from app.execution_coordinator.context_provider import CoordinationContext
from app.order_compliance.account_state_builder import build_account_state


@dataclass(frozen=True)
class RuntimeContextAssembler:
    """Assembles a coordination context from authoritative runtime inputs."""

    account_state_builder: Callable[..., Any] = build_account_state

    def build(self, *args: Any, **kwargs: Any) -> CoordinationContext:
        raise NotImplementedError(
            "Runtime context assembly is not implemented yet."
        )

import pytest

from app.execution_coordinator.runtime_context_assembler import (
    RuntimeContextAssembler,
)
from app.order_compliance.account_state_builder import build_account_state


def test_runtime_context_assembler_is_placeholder() -> None:
    assembler = RuntimeContextAssembler()

    with pytest.raises(
        NotImplementedError,
        match="Runtime context assembly is not implemented yet",
    ):
        assembler.build()


def test_runtime_context_assembler_uses_default_account_state_builder() -> None:
    assembler = RuntimeContextAssembler()

    assert assembler.account_state_builder is build_account_state

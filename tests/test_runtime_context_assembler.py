import pytest

from app.execution_coordinator.runtime_context_assembler import (
    RuntimeContextAssembler,
)


def test_runtime_context_assembler_is_placeholder() -> None:
    assembler = RuntimeContextAssembler()

    with pytest.raises(
        NotImplementedError,
        match="Runtime context assembly is not implemented yet",
    ):
        assembler.build()

from app.composition.execution_pipeline import (
    create_paper_execution_pipeline,
)
from app.execution_coordinator import ExecutionCoordinator


def test_create_paper_execution_pipeline_returns_coordinator() -> None:
    coordinator = create_paper_execution_pipeline()

    assert isinstance(coordinator, ExecutionCoordinator)


def test_create_paper_execution_pipeline_returns_fresh_coordinator() -> None:
    first = create_paper_execution_pipeline()
    second = create_paper_execution_pipeline()

    assert first is not second

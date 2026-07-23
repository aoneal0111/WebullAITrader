from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal

import app.paper_order_book as api
import app.paper_order_book.dispatcher as command_dispatcher
import pytest
from app.paper_order_book.execution_trace import COMPLETED, FAILED
from tests.paper_order_book.helpers import NOW, make_request


def _submit(command_id: str, order_id: str, seconds: int):
    return api.create_submit_command(
        command_id=command_id,
        order_id=order_id,
        occurred_at=NOW + timedelta(seconds=seconds),
        symbol="AAPL",
        asset_class="STOCK",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("1"),
        time_in_force="DAY",
    )


def test_successful_execution_summary_is_derived_from_trace() -> None:
    commands = (
        _submit("SUBMIT-1", "ORDER-2", 1),
        _submit("SUBMIT-2", "ORDER-3", 2),
    )
    orchestrator = api.PaperOrderBookOrchestrator()

    orchestrator.execute(make_request(commands=commands))

    summary = orchestrator._execution_summary
    assert summary.total_commands == len(orchestrator._execution_trace) == 2
    assert summary.completed_commands == 2
    assert summary.failed_commands == 0
    assert summary.outcome == COMPLETED
    with pytest.raises(FrozenInstanceError):
        summary.outcome = FAILED


def test_failed_execution_summary_preserves_trace_and_exception(
    monkeypatch,
) -> None:
    commands = (
        _submit("SUBMIT-1", "ORDER-2", 1),
        _submit("SUBMIT-2", "ORDER-3", 2),
    )
    orchestrator = api.PaperOrderBookOrchestrator()
    failure = RuntimeError("lifecycle failure")
    original_dispatch = command_dispatcher.dispatch_command
    calls = 0

    def fail_second_dispatch(order_book, command):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure
        return original_dispatch(order_book, command)

    monkeypatch.setattr(
        command_dispatcher,
        "dispatch_command",
        fail_second_dispatch,
    )

    with pytest.raises(RuntimeError) as caught:
        orchestrator.execute(make_request(commands=commands))

    summary = orchestrator._execution_summary
    assert caught.value is failure
    assert summary.total_commands == len(orchestrator._execution_trace) == 2
    assert summary.completed_commands == 1
    assert summary.failed_commands == 1
    assert summary.outcome == FAILED
    assert tuple(
        entry.outcome for entry in orchestrator._execution_trace
    ) == (COMPLETED, FAILED)


def test_summary_is_internal_and_absent_from_serialization() -> None:
    orchestrator = api.PaperOrderBookOrchestrator()
    result = orchestrator.execute(make_request(commands=()))

    assert orchestrator._execution_summary.total_commands == 0
    assert orchestrator._execution_summary.outcome == COMPLETED
    assert "PaperOrderBookExecutionSummary" not in api.__all__
    assert "execution_summary" not in api.serialize_result(result)

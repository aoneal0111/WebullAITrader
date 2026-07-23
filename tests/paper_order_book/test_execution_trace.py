from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal

import app.paper_order_book as api
import pytest
from app.paper_order_book.execution_trace import DISPATCHED
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


def test_each_executed_command_produces_one_ordered_trace_entry() -> None:
    commands = (
        _submit("SUBMIT-1", "ORDER-2", 1),
        _submit("SUBMIT-2", "ORDER-3", 2),
    )
    orchestrator = api.PaperOrderBookOrchestrator()

    orchestrator.execute(make_request(commands=commands))

    assert len(orchestrator._execution_trace) == len(commands)
    assert tuple(
        entry.command_type for entry in orchestrator._execution_trace
    ) == tuple(command.command_type for command in commands)
    assert tuple(
        entry.occurred_at for entry in orchestrator._execution_trace
    ) == tuple(command.occurred_at for command in commands)
    assert all(
        entry.stage == DISPATCHED
        for entry in orchestrator._execution_trace
    )
    with pytest.raises(FrozenInstanceError):
        orchestrator._execution_trace[0].stage = "changed"


def test_trace_is_internal_immutable_and_does_not_change_results() -> None:
    command = _submit("SUBMIT-1", "ORDER-2", 1)
    traced_request = make_request(commands=(command,))
    equivalent_request = make_request(
        commands=(_submit("SUBMIT-1", "ORDER-2", 1),)
    )
    orchestrator = api.PaperOrderBookOrchestrator()

    traced_result = orchestrator.execute(traced_request)
    equivalent_result = api.PaperOrderBookOrchestrator().execute(
        equivalent_request
    )

    assert isinstance(orchestrator._execution_trace, tuple)
    assert api.serialize_result(traced_result) == api.serialize_result(
        equivalent_result
    )
    assert "PaperOrderBookExecutionTraceEntry" not in api.__all__
    assert "execution_trace" not in api.serialize_result(traced_result)

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.execution import PaperExecutionEngine
from app.outcomes import OutcomePolicy, OutcomeRequest
from tests.execution.helpers import execution_request

STAMP = datetime(2026, 7, 21, 20, 3, tzinfo=UTC)


def execution(**changes):
    return PaperExecutionEngine().execute(execution_request(**changes))


def outcome_request(execution_value=None, policy=None, **changes):
    result = execution_value or execution()
    values = {"execution_result": result, "exit_price": Decimal("12.50"),
              "timestamp": max(STAMP, result.timestamp + timedelta(minutes=1)),
              "policy": policy or OutcomePolicy()}
    values.update(changes)
    return OutcomeRequest(**values)

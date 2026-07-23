from datetime import UTC, datetime, timedelta
from decimal import Decimal

import app.paper_order_book as api

SUBMITTED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
REJECTED_AT = SUBMITTED_AT + timedelta(seconds=1)


def test_public_reject_command_validates_dispatches_and_executes() -> None:
    identity = api.PaperOrderBookIdentity("REJECT-BOOK")
    policy = api.PaperOrderBookPolicy()
    submit = api.create_submit_command(
        command_id="SUBMIT-1",
        order_id="ORDER-1",
        occurred_at=SUBMITTED_AT,
        symbol="AAPL",
        asset_class="STOCK",
        side="BUY",
        order_type="LIMIT",
        quantity=Decimal("10"),
        time_in_force="DAY",
        limit_price=Decimal("101.25"),
    )
    submit_request = api.create_request(
        identity=identity,
        policy=policy,
        requested_at=SUBMITTED_AT,
        completed_at=SUBMITTED_AT,
        commands=(submit,),
    )
    submit_result = api.execute(submit_request)
    submitted_order = submit_result.snapshot.order_book.history()[0]
    reject = api.create_reject_command(
        command_id="REJECT-1",
        order=submitted_order,
        reason="Risk limit exceeded",
        occurred_at=REJECTED_AT,
    )
    reject_request = api.PaperOrderBookRequest(
        identity=identity,
        snapshot=submit_result.snapshot,
        commands=(reject,),
        requested_at=REJECTED_AT,
        completed_at=REJECTED_AT,
        policy=policy,
    )

    criteria = api.validate_request(reject_request)
    result = api.execute(reject_request)
    rejected_order = result.snapshot.order_book.history()[0]

    assert criteria.accepted is True
    assert result.criteria.accepted is True
    assert result.commands[0] is reject
    assert reject.payload.order is submitted_order
    assert rejected_order.status.value == "REJECTED"
    assert rejected_order.rejection_reason == "Risk limit exceeded"
    assert rejected_order.request is submitted_order.request
    assert rejected_order.created_at is submitted_order.created_at
    assert rejected_order.updated_at is REJECTED_AT

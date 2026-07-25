from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.operations.paper_lifecycle import PaperRuntimeSession
from app.paper_session import close_paper_session, create_paper_session


STARTED_AT = datetime(2026, 1, 1, 14, 30, tzinfo=UTC)
STOPPED_AT = datetime(2026, 1, 1, 15, 30, tzinfo=UTC)


def test_paper_runtime_session_starts_fresh_session() -> None:
    lifecycle = PaperRuntimeSession(
        session_id="paper-1",
        initial_cash=Decimal("100000"),
        clock=lambda: STARTED_AT,
    )

    session = lifecycle.start()

    assert lifecycle.session is session
    assert lifecycle.active is True
    assert session.session_id == "paper-1"
    assert session.started_at == STARTED_AT
    assert session.status.value == "ACTIVE"


def test_paper_runtime_session_closes_owned_session() -> None:
    timestamps = iter((STARTED_AT, STOPPED_AT))
    lifecycle = PaperRuntimeSession(
        session_id="paper-1",
        initial_cash=Decimal("100000"),
        clock=lambda: next(timestamps),
    )
    lifecycle.start()

    session = lifecycle.close()

    assert session is lifecycle.session
    assert session is not None
    assert session.status.value == "CLOSED"
    assert session.ended_at == STOPPED_AT
    assert lifecycle.active is False


def test_paper_runtime_session_context_closes_active_session() -> None:
    timestamps = iter((STARTED_AT, STOPPED_AT))
    lifecycle = PaperRuntimeSession(
        session_id="paper-1",
        initial_cash=Decimal("100000"),
        clock=lambda: next(timestamps),
    )

    with lifecycle:
        lifecycle.start()
        assert lifecycle.active is True

    assert lifecycle.session is not None
    assert lifecycle.session.status.value == "CLOSED"


def test_paper_runtime_session_recovers_active_session() -> None:
    session = create_paper_session(
        session_id="paper-1",
        initial_cash=Decimal("100000"),
        started_at=STARTED_AT,
    )
    lifecycle = PaperRuntimeSession(
        session_id="paper-1",
        initial_cash=Decimal("100000"),
        clock=lambda: STOPPED_AT,
    )

    recovered = lifecycle.recover(session)

    assert recovered is session
    assert lifecycle.session is session
    assert lifecycle.active is True


def test_paper_runtime_session_rejects_mismatched_recovery() -> None:
    session = create_paper_session(
        session_id="other-session",
        initial_cash=Decimal("100000"),
        started_at=STARTED_AT,
    )
    lifecycle = PaperRuntimeSession(
        session_id="paper-1",
        initial_cash=Decimal("100000"),
        clock=lambda: STOPPED_AT,
    )

    with pytest.raises(
        ValueError,
        match="recovered paper session ID does not match",
    ):
        lifecycle.recover(session)


def test_paper_runtime_session_rejects_closed_recovery() -> None:
    session = create_paper_session(
        session_id="paper-1",
        initial_cash=Decimal("100000"),
        started_at=STARTED_AT,
    )
    closed_session = close_paper_session(
        session,
        ended_at=STOPPED_AT,
    )
    lifecycle = PaperRuntimeSession(
        session_id="paper-1",
        initial_cash=Decimal("100000"),
        clock=lambda: STOPPED_AT,
    )

    with pytest.raises(
        ValueError,
        match="recovered paper session must be active",
    ):
        lifecycle.recover(closed_session)

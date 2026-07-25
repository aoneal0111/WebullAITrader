"""Lifecycle management for one paper-trading runtime session."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.paper_session import (
    PaperTradingSession,
    close_paper_session,
    create_paper_session,
)


Clock = Callable[[], datetime]


@dataclass(slots=True)
class PaperRuntimeSession:
    """Own creation, recovery, and closure of one paper session."""

    session_id: str
    initial_cash: Decimal
    clock: Clock
    session: PaperTradingSession | None = None

    def __post_init__(self) -> None:
        normalized_session_id = self.session_id.strip()
        if not normalized_session_id:
            raise ValueError("session_id must not be empty")
        self.session_id = normalized_session_id

        if self.initial_cash < Decimal("0"):
            raise ValueError("initial_cash must be nonnegative")

        if not callable(self.clock):
            raise TypeError("clock must be callable")

        if self.session is not None:
            self._validate_recovery(self.session)

    def __enter__(self) -> PaperRuntimeSession:
        return self

    @property
    def active(self) -> bool:
        return (
            self.session is not None
            and self.session.status.value == "ACTIVE"
        )

    def start(self) -> PaperTradingSession:
        """Create and own a fresh active paper session."""

        if self.session is not None:
            raise RuntimeError("paper runtime session has already been initialized")

        self.session = create_paper_session(
            session_id=self.session_id,
            initial_cash=self.initial_cash,
            started_at=self.clock(),
        )
        return self.session

    def recover(
        self,
        session: PaperTradingSession,
    ) -> PaperTradingSession:
        """Take lifecycle ownership of an existing active paper session."""

        if self.session is not None:
            raise RuntimeError("paper runtime session has already been initialized")

        self._validate_recovery(session)
        self.session = session
        return session

    def close(self) -> PaperTradingSession | None:
        """Close the owned session when it remains active."""

        if not self.active:
            return self.session

        assert self.session is not None
        self.session = close_paper_session(
            self.session,
            ended_at=self.clock(),
        )
        return self.session

    def __exit__(
        self,
        exception_type,
        exception,
        traceback,
    ) -> None:
        self.close()

    def _validate_recovery(
        self,
        session: PaperTradingSession,
    ) -> None:
        if session.session_id != self.session_id:
            raise ValueError("recovered paper session ID does not match")
        if session.status.value != "ACTIVE":
            raise ValueError("recovered paper session must be active")


__all__ = ["PaperRuntimeSession"]

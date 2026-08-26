"""Composition root for desktop paper-order commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from collections.abc import Callable

from app.authentication.models import (
    AuthenticationRequest,
    AuthenticationStatus,
)
from app.authentication.policies import AuthenticationPolicy
from app.authentication.service import DeterministicAuthenticationService
from app.credentials.models import CredentialResponse
from app.order_cancellation.policies import OrderCancellationPolicy
from app.order_cancellation.runtime import DeterministicOrderCancellationRuntime
from app.order_placement.policies import OrderPlacementPolicy
from app.order_placement.runtime import DeterministicOrderPlacementRuntime
from app.paper_gateway import PaperOrderGateway
from app.paper_gateway.durable_store import DurablePaperExecutionStore
from app.operations.runtime import RuntimeEventSink
from app.paper_trading.execution_engine import PaperExecutionEngine
from app.paper_trading.order_book import PaperOrderBook
from app.services.order_command_factory import OrderCommandFactory
from app.services.trading_service import TradingService
from app.session.manager import DeterministicSessionManager
from app.session.models import (
    SessionIdentifier,
    SessionRequest,
    SessionStatus,
)
from app.session.policies import SessionPolicy

PAPER_ACCOUNT_ID = "paper-account"
PAPER_SESSION_ID = "desktop-paper-session"


class _PaperCredentialProvider:
    """Provide a non-secret identity used only by the local paper runtime."""

    def provide(self, request: object) -> CredentialResponse:
        return CredentialResponse(
            "paper",
            "desktop-paper-trading",
            {"identity": "local-paper-user"},
        )


class _PaperAuthenticationVerifier:
    """Accept only the deterministic local paper credential response."""

    def verify(
        self,
        request: AuthenticationRequest,
        credentials: CredentialResponse,
    ) -> bool:
        return (
            request.broker_identifier == "paper"
            and request.credential_purpose == "desktop-paper-trading"
            and credentials.broker_identifier == "paper"
            and credentials.credential_purpose == "desktop-paper-trading"
        )


@dataclass(frozen=True, slots=True)
class PaperTradingCommandComposition:
    """Long-lived command dependencies for desktop paper trading."""

    authentication_service: DeterministicAuthenticationService
    session_manager: DeterministicSessionManager
    order_book: PaperOrderBook
    execution_engine: PaperExecutionEngine
    gateway: PaperOrderGateway
    placement_runtime: DeterministicOrderPlacementRuntime
    cancellation_runtime: DeterministicOrderCancellationRuntime
    trading_service: TradingService
    order_command_factory: OrderCommandFactory
    session_id: str
    account_id: str
    durable_store: DurablePaperExecutionStore | None = None

    def close(self) -> None:
        """Invalidate the local paper command session and authentication."""

        if self.session_manager.state().status is SessionStatus.ACTIVE:
            self.session_manager.invalidate()
        if (
            self.authentication_service.state().status
            is AuthenticationStatus.AUTHENTICATED
        ):
            self.authentication_service.logout()
        if self.durable_store is not None:
            self.durable_store.close()


def create_paper_trading_command_composition(
    *,
    order_book: PaperOrderBook | None = None,
    session_id: str = PAPER_SESSION_ID,
    account_id: str = PAPER_ACCOUNT_ID,
    event_sink: RuntimeEventSink | None = None,
    position_average_cost_source: (
        Callable[[str], Decimal | None] | None
    ) = None,
    position_quantity_source: Callable[[str], Decimal] | None = None,
    clock: Callable[[], datetime] | None = None,
    persistence_path: str | None = None,
) -> PaperTradingCommandComposition:
    """Build an authenticated, active, paper-only trading command graph."""

    authentication_service = DeterministicAuthenticationService(
        _PaperCredentialProvider(),
        _PaperAuthenticationVerifier(),
        AuthenticationPolicy(),
    )
    authentication_service.authenticate(
        AuthenticationRequest(
            broker_identifier="paper",
            credential_purpose="desktop-paper-trading",
            required_value_names=("identity",),
            metadata={"source": "desktop_composition"},
        )
    )

    session_manager = DeterministicSessionManager(
        authentication_service,
        SessionPolicy(),
    )
    session_manager.create(
        SessionRequest(
            identifier=SessionIdentifier(session_id),
            purpose="desktop-paper-trading",
            metadata={"account_id": account_id},
        )
    )
    session_manager.activate()

    shared_order_book = order_book or PaperOrderBook()
    durable_store = (
        None
        if persistence_path is None
        else DurablePaperExecutionStore(persistence_path, account_id=account_id)
    )
    execution_engine = PaperExecutionEngine(shared_order_book)
    gateway_arguments = {
        "execution_engine": execution_engine,
        "event_sink": event_sink,
        "position_average_cost_source": position_average_cost_source,
        "position_quantity_source": position_quantity_source,
        "durable_store": durable_store,
    }
    if clock is not None:
        gateway_arguments["clock"] = clock
    gateway = PaperOrderGateway(
        shared_order_book,
        **gateway_arguments,
    )
    placement_runtime = DeterministicOrderPlacementRuntime(
        session_manager,
        gateway,
        OrderPlacementPolicy(enabled=True),
    )
    cancellation_runtime = DeterministicOrderCancellationRuntime(
        session_manager,
        gateway,
        OrderCancellationPolicy(enabled=True),
    )
    trading_service = TradingService(
        placement_runtime,
        cancellation_runtime,
    )
    order_command_factory = OrderCommandFactory(
        session_id_provider=lambda: session_id,
        account_id_provider=lambda: account_id,
    )

    return PaperTradingCommandComposition(
        authentication_service=authentication_service,
        session_manager=session_manager,
        order_book=shared_order_book,
        execution_engine=execution_engine,
        gateway=gateway,
        placement_runtime=placement_runtime,
        cancellation_runtime=cancellation_runtime,
        trading_service=trading_service,
        order_command_factory=order_command_factory,
        session_id=session_id,
        account_id=account_id,
        durable_store=durable_store,
    )


__all__ = [
    "PAPER_ACCOUNT_ID",
    "PAPER_SESSION_ID",
    "PaperTradingCommandComposition",
    "create_paper_trading_command_composition",
]

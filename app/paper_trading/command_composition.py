"""Composition root for desktop paper-order commands."""

from __future__ import annotations

from dataclasses import dataclass

from app.authentication.models import AuthenticationRequest
from app.authentication.policies import AuthenticationPolicy
from app.authentication.service import DeterministicAuthenticationService
from app.credentials.models import CredentialResponse
from app.order_cancellation.policies import OrderCancellationPolicy
from app.order_cancellation.runtime import DeterministicOrderCancellationRuntime
from app.order_placement.policies import OrderPlacementPolicy
from app.order_placement.runtime import DeterministicOrderPlacementRuntime
from app.paper_gateway import PaperOrderGateway
from app.paper_trading.order_book import PaperOrderBook
from app.services.order_command_factory import OrderCommandFactory
from app.services.trading_service import TradingService
from app.session.manager import DeterministicSessionManager
from app.session.models import SessionIdentifier, SessionRequest
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
    gateway: PaperOrderGateway
    placement_runtime: DeterministicOrderPlacementRuntime
    cancellation_runtime: DeterministicOrderCancellationRuntime
    trading_service: TradingService
    order_command_factory: OrderCommandFactory
    session_id: str
    account_id: str


def create_paper_trading_command_composition(
    *,
    order_book: PaperOrderBook | None = None,
    session_id: str = PAPER_SESSION_ID,
    account_id: str = PAPER_ACCOUNT_ID,
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
    gateway = PaperOrderGateway(shared_order_book)
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
        gateway=gateway,
        placement_runtime=placement_runtime,
        cancellation_runtime=cancellation_runtime,
        trading_service=trading_service,
        order_command_factory=order_command_factory,
        session_id=session_id,
        account_id=account_id,
    )


__all__ = [
    "PAPER_ACCOUNT_ID",
    "PAPER_SESSION_ID",
    "PaperTradingCommandComposition",
    "create_paper_trading_command_composition",
]

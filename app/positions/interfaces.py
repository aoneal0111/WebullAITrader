from typing import Protocol
from app.positions.models import PositionModel, PositionsRequest, PositionsResult


class BrokerPositionGateway(Protocol):
    def get_positions(self, request: PositionsRequest) -> tuple[PositionModel, ...]: ...


class PositionsRuntime(Protocol):
    def get_positions(self, request: PositionsRequest) -> PositionsResult: ...

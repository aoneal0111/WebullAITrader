import asyncio
from typing import Any

import pytest

from app.broker.snapshot import AccountSnapshotService
from app.exceptions import MCPResponseError


class MockAccountClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def get_accounts(self) -> list[dict[str, Any]]:
        self.calls.append(("get_account_list", None))
        return [{"account_id": "internal-id-5678", "account_number": "123456789"}]

    async def get_balance(self, account_id: str) -> dict[str, Any]:
        self.calls.append(("get_account_balance", account_id))
        return {"total_value": "1050.25", "cash": "50.25", "buying_power": "100.50", "currency": "USD"}

    async def get_positions(self, account_id: str) -> list[dict[str, Any]]:
        self.calls.append(("get_account_positions", account_id))
        return [{"symbol": "TEST", "quantity": "2", "market_value": "1000", "average_price": "450"}]


def test_snapshot_uses_only_account_read_operations() -> None:
    client = MockAccountClient()
    snapshots = asyncio.run(AccountSnapshotService(client).retrieve())
    assert [call[0] for call in client.calls] == [
        "get_account_list", "get_account_balance", "get_account_positions"
    ]
    assert snapshots[0].positions[0].symbol == "TEST"
    assert snapshots[0].redacted_dict()["account"] == "****6789"


def test_missing_account_id_is_rejected() -> None:
    client = MockAccountClient()

    async def missing_id() -> list[dict[str, Any]]:
        return [{"account_number": "123456789"}]

    client.get_accounts = missing_id  # type: ignore[method-assign]
    with pytest.raises(MCPResponseError, match="account ID"):
        asyncio.run(AccountSnapshotService(client).retrieve())

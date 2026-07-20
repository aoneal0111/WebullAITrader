import asyncio
import json
from typing import Any

from app.cli import run


class MockClient:
    async def get_accounts(self) -> list[dict[str, Any]]:
        return [{"account_id": "secret-internal-id", "account_number": "123456789"}]

    async def get_balance(self, account_id: str) -> dict[str, Any]:
        return {"cash": "25.00", "currency": "USD"}

    async def get_positions(self, account_id: str) -> list[dict[str, Any]]:
        return []


def test_cli_summary_does_not_contain_raw_identifiers() -> None:
    output = json.dumps(asyncio.run(run(MockClient())))
    assert "123456789" not in output
    assert "secret-internal-id" not in output
    assert "****6789" in output

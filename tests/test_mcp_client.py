import asyncio

import pytest

from app.broker.mcp_client import READ_ONLY_TOOLS, WebullMCPClient
from app.exceptions import MCPTransportError


def test_only_required_read_tools_are_allowlisted() -> None:
    assert READ_ONLY_TOOLS == {
        "get_account_list",
        "get_account_balance",
        "get_account_positions",
    }


def test_non_read_tool_is_rejected_before_transport() -> None:
    client = WebullMCPClient("http://invalid.local/mcp")
    with pytest.raises(MCPTransportError, match="not permitted"):
        asyncio.run(client._call_read_tool("place_order", {}))

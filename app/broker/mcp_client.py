from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.exceptions import MCPResponseError, MCPTransportError

READ_ONLY_TOOLS = frozenset(
    {"get_account_list", "get_account_balance", "get_account_positions"}
)


class AccountDataClient(Protocol):
    async def get_accounts(self) -> list[dict[str, Any]]: ...
    async def get_balance(self, account_id: str) -> dict[str, Any]: ...
    async def get_positions(self, account_id: str) -> list[dict[str, Any]]: ...


class WebullMCPClient:
    """A deliberately narrow client that can invoke only account read operations."""

    def __init__(self, url: str, timeout_seconds: float = 30.0) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds

    async def get_accounts(self) -> list[dict[str, Any]]:
        value = await self._call_read_tool("get_account_list", {})
        return _as_object_list(value, "account list")

    async def get_balance(self, account_id: str) -> dict[str, Any]:
        value = await self._call_read_tool("get_account_balance", {"account_id": account_id})
        return _as_object(value, "account balance")

    async def get_positions(self, account_id: str) -> list[dict[str, Any]]:
        value = await self._call_read_tool("get_account_positions", {"account_id": account_id})
        return _as_object_list(value, "account positions")

    async def _call_read_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        if name not in READ_ONLY_TOOLS:
            raise MCPTransportError(f"MCP tool is not permitted: {name}")
        try:
            with anyio.fail_after(self._timeout_seconds):
                async with streamable_http_client(self._url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(name, dict(arguments))
        except Exception as exc:
            raise MCPTransportError(f"Webull MCP read failed for {name}") from exc
        if result.isError:
            raise MCPTransportError(f"Webull MCP reported an error for {name}")
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return structured.get("result", structured)
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    continue
        raise MCPResponseError(f"Webull MCP returned no JSON data for {name}")


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("data", "items", "accounts", "positions", "result"):
            if key in value:
                return value[key]
    return value


def _as_object(value: Any, label: str) -> dict[str, Any]:
    value = _unwrap(value)
    if not isinstance(value, dict):
        raise MCPResponseError(f"Malformed {label} response")
    return value


def _as_object_list(value: Any, label: str) -> list[dict[str, Any]]:
    value = _unwrap(value)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise MCPResponseError(f"Malformed {label} response")
    return value

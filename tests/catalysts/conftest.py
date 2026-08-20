from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest


@pytest.fixture(autouse=True)
def prohibit_real_network_calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail the catalyst suite if an httpx request reaches real transport."""

    attempts: list[str] = []

    def blocked_send(client: httpx.Client, request: httpx.Request, **kwargs):
        attempts.append(f"{request.method} {request.url.host}")
        raise httpx.ConnectError("real network is disabled in catalyst tests")

    monkeypatch.setattr(httpx.Client, "send", blocked_send)
    yield
    assert attempts == [], "catalyst test attempted real network: " + ", ".join(
        attempts
    )

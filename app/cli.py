from __future__ import annotations

import asyncio
import json

import structlog

from app.broker.mcp_client import AccountDataClient, WebullMCPClient
from app.broker.snapshot import AccountSnapshotService
from app.config import Settings
from app.exceptions import WebullAgentError
from app.logging_config import configure_logging


async def run(client: AccountDataClient) -> list[dict[str, object]]:
    snapshots = await AccountSnapshotService(client).retrieve()
    return [snapshot.redacted_dict() for snapshot in snapshots]


def main() -> int:
    try:
        settings = Settings.from_env()
        configure_logging(settings.log_level)
        client = WebullMCPClient(settings.webull_mcp_url, settings.mcp_timeout_seconds)
        summary = asyncio.run(run(client))
        print(json.dumps({"accounts": summary}, indent=2, sort_keys=True))
        return 0
    except WebullAgentError as exc:
        configure_logging()
        structlog.get_logger(__name__).error("account_snapshot_failed", error=str(exc))
        return 1
    except KeyboardInterrupt:
        return 130

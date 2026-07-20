from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from app.exceptions import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    webull_mcp_url: str
    log_level: str = "INFO"
    mcp_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        url = os.getenv("WEBULL_MCP_URL", "").strip()
        if not url:
            raise ConfigurationError("WEBULL_MCP_URL is required")
        try:
            timeout = float(os.getenv("MCP_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise ConfigurationError("MCP_TIMEOUT_SECONDS must be numeric") from exc
        if timeout <= 0:
            raise ConfigurationError("MCP_TIMEOUT_SECONDS must be greater than zero")
        return cls(url, os.getenv("LOG_LEVEL", "INFO").upper(), timeout)

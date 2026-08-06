"""Safely verify Atlas DataClient authentication without exposing SDK logs."""

from __future__ import annotations

import json
import logging

from app.configuration import load_configuration
from app.webull.client_factories import MarketDataClientFactory


def main() -> int:
    result = {
        "atlas_data_client_initialized": False,
        "sanitized_error_code": "",
        "exception_type": "",
    }
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        configuration = load_configuration().market_data
        result["atlas_data_client_initialized"] = (
            MarketDataClientFactory(configuration).create() is not None
        )
    except Exception as exc:
        code = str(getattr(exc, "error_code", "") or "")
        result["sanitized_error_code"] = (
            code if code.replace("_", "").isalnum() else "REDACTED"
        )
        result["exception_type"] = type(exc).__name__
    finally:
        logging.disable(previous_disable)
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline Grok configuration check: no GUI, requests, workers or orders."""
import json
import os

from dotenv import load_dotenv
from app.asset_modules.grok import configured_client
from app.configuration.loader import load_configuration
from app.composition.grok_paper import create_grok_paper_suite
from app.composition.runtime_mode import RuntimeMode


def main():
    load_dotenv(override=False)
    c = load_configuration()
    status = {"scope": "OFFLINE_GROK_CONFIGURATION_NO_NETWORK_OR_TRADING",
        "entries_enabled": c.grok_paper_entries_enabled,
        "key_present": bool(os.environ.get("XAI_API_KEY", "").strip()),
        "model_present": bool(os.environ.get("ATLAS_GROK_MODEL", "").strip()),
        "credentials_validated_online": False}
    try:
        suite = create_grok_paper_suite(c, RuntimeMode.PAPER)
        client = suite.client if suite else configured_client()
        status.update(provider_constructible=client is not None, requests=0,
            entry_composition_configured=suite is not None)
    except ValueError:
        status.update(provider_constructible=False, requests=0,
            entry_composition_configured=False, configuration_error=True)
        print(json.dumps(status, indent=2))
        return 1
    print(json.dumps(status, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

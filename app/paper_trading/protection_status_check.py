"""Read-only profile/schema diagnostic; offline unless --online is supplied."""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path

from app.webull.protection_status_probe import ProtectionStatusProbe, event_protocol_observation, validate_symbols


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--symbols", default="SPY,QQQ", help="One to five uppercase equity symbols")
    args = parser.parse_args(argv)
    symbols = tuple(args.symbols.split(","))
    try:
        validate_symbols(symbols)
    except ValueError as exc:
        parser.error(str(exc))
    report = dict(scope="READ_ONLY_PROFILE_STATUS_NOT_HALT_CHANNEL_VALIDATION",
                  sdk_version=version("webull-openapi-python-sdk"), online=args.online,
                  requested_symbols=list(symbols), entries_enabled=False,
                  live_feed_validated=False, status_authorization="NONE", sdk_operation_attempts=0,
                  orders=0, installed_event_protocol=event_protocol_observation())
    if not args.online:
        report.update(state="OFFLINE_NO_REQUESTS", network_requests=0)
        print(json.dumps(report, indent=2))
        return 0
    from dotenv import load_dotenv
    from app.configuration.loader import load_configuration
    from app.webull.client_factories import market_data_configuration
    from app.webull.options_data_probe import safe_exception_details
    from app.webull.sdk_market_data import create_official_data_client
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    try:
        c = market_data_configuration(load_configuration())
        if not c.api_key.strip() or not c.api_secret.strip():
            report["state"] = "MARKET_DATA_CREDENTIALS_MISSING"
            print(json.dumps(report, indent=2))
            return 1
        probe = ProtectionStatusProbe(lambda: create_official_data_client(
            app_key=c.api_key, app_secret=c.api_secret, endpoint=c.api_base_url,
            timeout_seconds=5), clock=lambda: datetime.now(timezone.utc))
        result = probe.profiles(symbols)
        observed = result.report()
        observed.pop("quote_readiness", None)
        report.update(environment=c.environment.value, sdk_operation_attempts=probe.calls,
                      result=observed, state=result.state,
                      next_step="VERIFY_STATUS_FRESHNESS_AND_HALT_DELIVERY_BEFORE_FEED_ADAPTER")
        print(json.dumps(report, indent=2))
        return 0 if result.state == "RESPONSE_CAPTURED" else 1
    except Exception as exc:
        report.update(state="CONFIGURATION_OR_PROBE_UNAVAILABLE", diagnostics=safe_exception_details(exc))
        print(json.dumps(report, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

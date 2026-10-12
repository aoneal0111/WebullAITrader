"""Explicit online read-only option-data check; offline by default."""
import argparse
from app.webull.options_data_probe import supported_symbol
import json
from importlib.metadata import version

from app.webull.options_data_probe import OptionsDataProbe
from app.webull.market_data_session import utc_now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--online', action='store_true')
    parser.add_argument('--symbols', help='One or two comma-separated SPY/QQQ OCC contracts; reads each separately')
    args = parser.parse_args()
    supplied = tuple(s.strip() for s in args.symbols.split(',')) if args.symbols else ()
    if args.symbols and (not args.online or not 1 <= len(supplied) <= 2
            or len(set(supplied)) != len(supplied) or not all(supported_symbol(s) for s in supplied)):
        parser.error('--symbols requires --online and one or two unique SPY/QQQ OCC contracts')
    report = {'scope':'READ_ONLY_OPTIONS_SCHEMA_CHECK_NO_ORDERS',
        'sdk_version':version('webull-openapi-python-sdk'), 'online':args.online,
        'options_trading_enabled':False, 'quote_readiness':'UNVALIDATED_RESPONSE_SCHEMA'}
    if not args.online:
        report['market_data_requests'] = 0
        print(json.dumps(report,indent=2))
        return 0
    from dotenv import load_dotenv
    from app.configuration.loader import load_configuration
    from app.webull.client_factories import market_data_configuration
    from app.webull.sdk_market_data import create_official_data_client
    load_dotenv(override=False)
    c = market_data_configuration(load_configuration())
    if not c.api_key.strip() or not c.api_secret.strip():
        report.update(state='MARKET_DATA_CREDENTIALS_MISSING',market_data_requests=0)
        print(json.dumps(report,indent=2))
        return 1
    probe = OptionsDataProbe(lambda: create_official_data_client(app_key=c.api_key,
        app_secret=c.api_secret,endpoint=c.api_base_url,timeout_seconds=5),clock=utc_now)
    if supplied:
        reports = [probe.snapshots((s,)) for s in supplied]
    else:
        reports = [probe.discover(s) for s in ('SPY','QQQ')]
        symbols = tuple(sorted({s for r in reports for s in r.symbols}))[:20]
        if symbols:
            reports.append(probe.snapshots(symbols))
    report.update(environment=c.environment.value,market_data_requests=probe.calls,
        results=[r.report() for r in reports],
        next_step='VERIFY_RESPONSE_FIELDS_AND_REAL_TIME_ENTITLEMENT_BEFORE_QUOTE_ADAPTER')
    print(json.dumps(report,indent=2))
    return 0 if all(r.state=='RESPONSE_CAPTURED' for r in reports) else 1


if __name__ == '__main__':
    raise SystemExit(main())

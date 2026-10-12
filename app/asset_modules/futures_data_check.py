"""Offline by default; explicit read-only online contract/schema probe."""
import argparse
from importlib.metadata import version
import json

from app.webull.futures_data_probe import FuturesDataProbe, supported_contract
from app.webull.market_data_session import utc_now


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--online',action='store_true')
    parser.add_argument('--symbol',help='One actual MES/MNQ expiry contract from discovery')
    args=parser.parse_args()
    if args.symbol and (not args.online or not supported_contract(args.symbol)):
        parser.error('--symbol requires --online and one actual MES/MNQ expiry contract')
    report={'scope':'READ_ONLY_FUTURES_SCHEMA_CHECK_NO_ORDERS',
        'sdk_version':version('webull-openapi-python-sdk'),'online':args.online,
        'futures_trading_enabled':False,'quote_readiness':'UNVALIDATED_RESPONSE_SCHEMA'}
    if not args.online:
        report['market_data_requests']=0
        print(json.dumps(report,indent=2)); return 0
    from dotenv import load_dotenv
    from app.configuration.loader import load_configuration
    from app.webull.client_factories import market_data_configuration
    from app.webull.sdk_market_data import create_official_data_client
    load_dotenv(override=False)
    c=market_data_configuration(load_configuration())
    if not c.api_key.strip() or not c.api_secret.strip():
        report.update(state='MARKET_DATA_CREDENTIALS_MISSING',market_data_requests=0)
        print(json.dumps(report,indent=2)); return 1
    probe=FuturesDataProbe(lambda:create_official_data_client(app_key=c.api_key,app_secret=c.api_secret,
        endpoint=c.api_base_url,timeout_seconds=5),clock=utc_now)
    if args.symbol:
        results=[probe.profile(args.symbol),probe.depth(args.symbol),probe.bars(args.symbol)]
    else:
        results=[probe.discover(p) for p in ('MES','MNQ')]
    report.update(environment=c.environment.value,market_data_requests=probe.calls,
        results=[r.report() for r in results],
        next_step='VERIFY_CONTRACT_EXPIRY_SPECS_TIMESTAMPS_AND_ENTITLEMENT_BEFORE_ADAPTER')
    print(json.dumps(report,indent=2))
    return 0 if all(r.state=='RESPONSE_CAPTURED' for r in results) else 1


if __name__=='__main__':
    raise SystemExit(main())

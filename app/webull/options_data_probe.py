"""Bounded read-only option SDK capability/schema probe; no order surface."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import re
from threading import Lock

OCC = re.compile(r'(SPY|QQQ)(\d{6})([CP])(\d{8})\Z')
SENSITIVE = re.compile(r'(?i)key|secret|token|password|signature|cookie|authorization|account')
DECIMAL_FIELDS = frozenset({'bid','ask','bid_price','ask_price','price','strike_price',
    'multiplier','contract_multiplier','option_contract_multiplier','tick_size','bid_size','ask_size'})
TIME_FIELDS = frozenset({'time','timestamp','bid_time','ask_time','quote_time',
    'bid_timestamp','ask_timestamp','expire_date','expiration_date','option_expire_date'})


def safe_exception_details(exc):
    """Only typed status and bounded provider codes; never exception messages."""
    from webull.core.exception.exceptions import ClientException, ServerException
    known = {'AttributeError', 'TypeError', 'ValueError', 'TimeoutError',
             'ConnectionError', 'OSError', 'JSONDecodeError'}
    name = type(exc).__name__
    details = {'exception_class': name if name in known else 'OTHER_EXCEPTION'}
    if isinstance(exc, (ClientException, ServerException)):
        details['exception_class'] = 'ServerException' if isinstance(exc,ServerException) else 'ClientException'
        code = exc.get_error_code()
        from webull.core.exception import error_code as sdk_codes
        permitted = {v for k,v in vars(sdk_codes).items() if k.startswith('SDK_') and isinstance(v,str)}
        permitted.update({'ERROR_INIT_TOKEN', 'ERROR_STORAGE_TOKEN', 'UNSUPPORTED_SYMBOL',
            'INVALID_SYMBOL', 'PERMISSION_DENIED', 'QUOTE_PERMISSION_DENIED',
            'SUBSCRIPTION_REQUIRED', 'DATA_PERMISSION_DENIED', 'ACCESS_DENIED',
            'INVALID_TOKEN', 'TOKEN_EXPIRED', 'RATE_LIMIT', 'TOO_MANY_REQUESTS'})
        details['error_code'] = code if isinstance(code,str) and code in permitted else 'UNRECOGNIZED_ERROR_CODE'
        if isinstance(exc,ServerException):
            status = exc.get_http_status()
            if type(status) is int and 100 <= status <= 599:
                details['http_status'] = status
    return details


def supported_symbol(value):
    if not isinstance(value, str) or not OCC.fullmatch(value):
        return False
    match = OCC.fullmatch(value)
    try:
        datetime.strptime(match[2], '%y%m%d')
        return int(match[4]) > 0
    except ValueError:
        return False


def schema_sample(value):
    """Show bounded structure and selected market values, never error bodies."""
    remaining = [512]
    symbols = set()
    def visit(v, depth=0, field=''):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 6:
            return {'truncated':True}
        if isinstance(v, dict):
            out = {}
            for k, x in list(v.items())[:64]:
                if not isinstance(k,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',k) or SENSITIVE.search(k):
                    continue
                out[k] = visit(x,depth+1,k)
            return out
        if isinstance(v,list):
            return {'count':len(v),'sample':[visit(x,depth+1,field) for x in v[:2]]}
        if field in {'symbol','option_symbol'} and supported_symbol(v):
            symbols.add(v)
            return v
        if field == 'underlying_symbol' and v in {'SPY','QQQ'}:
            return v
        if field in {'option_type','right'} and v in {'CALL','PUT','C','P'}:
            return v
        if field in DECIMAL_FIELDS and isinstance(v,(str,int,float)) and not isinstance(v,bool):
            try:
                d = Decimal(str(v))
                if d.is_finite() and len(str(v)) <= 40:
                    return str(v)
            except InvalidOperation:
                pass
        if field in TIME_FIELDS and isinstance(v,str) and len(v) <= 40:
            try:
                if v.isdigit() and 8 <= len(v) <= 16:
                    return v
                datetime.fromisoformat(v.replace('Z','+00:00'))
                return v
            except ValueError:
                pass
        return {'type':type(v).__name__}  # No arbitrary provider strings.
    return visit(value), tuple(sorted(symbols))


@dataclass(frozen=True)
class Observation:
    operation: str
    state: str
    observed_at: datetime
    schema: dict | list | None = None
    symbols: tuple[str,...] = ()
    diagnostics: dict | None = None
    requested_symbols: tuple[str,...] = ()

    def report(self):
        return {'operation':self.operation,'state':self.state,
            'observed_at':self.observed_at.isoformat(), 'schema':self.schema,
            'symbols':list(self.symbols), 'diagnostics':self.diagnostics,
            'requested_symbols':list(self.requested_symbols), 'quote_readiness':'UNVALIDATED_RESPONSE_SCHEMA'}


class OptionsDataProbe:
    def __init__(self, factory, *, clock):
        if not callable(factory) or not callable(clock):
            raise TypeError('Lazy read-only SDK factory and clock required')
        self.factory, self.clock = factory, clock
        self.client, self.calls, self.lock = None, 0, Lock()

    def _read(self, operation, invoke, *, requested_symbols=()):
        with self.lock:
            if self.calls >= 3:
                return Observation(operation,'PROBE_REQUEST_LIMIT',self.clock(), requested_symbols=requested_symbols)
            self.calls += 1  # No retries, including failed initialization.
            try:
                if self.client is None:
                    self.client = self.factory()
                response = invoke(self.client)
                status = getattr(response,'status_code',None)
                if status != 200:
                    return Observation(operation, f'HTTP_{status}' if type(status) is int else 'INVALID_RESPONSE',self.clock(), requested_symbols=requested_symbols)
                raw = getattr(response,'content',None)
                if isinstance(raw,bytes) and len(raw) > 131072:
                    return Observation(operation,'RESPONSE_TOO_LARGE',self.clock(), requested_symbols=requested_symbols)
                payload = response.json()
                if not isinstance(payload,(dict,list)):
                    return Observation(operation,'INVALID_RESPONSE',self.clock(), requested_symbols=requested_symbols)
                if len(json.dumps(payload,allow_nan=False).encode()) > 131072:
                    return Observation(operation,'RESPONSE_TOO_LARGE',self.clock(), requested_symbols=requested_symbols)
                sample,symbols = schema_sample(payload)
                if operation.startswith('CONTRACTS_'):
                    underlying = operation.removeprefix('CONTRACTS_')
                    symbols = tuple(s for s in symbols if OCC.fullmatch(s)[1] == underlying)
                return Observation(operation,'RESPONSE_CAPTURED',self.clock(),sample,symbols,
                    requested_symbols=requested_symbols)
            except Exception as exc:
                return Observation(operation,'SDK_REQUEST_UNAVAILABLE',self.clock(),
                    diagnostics=safe_exception_details(exc), requested_symbols=requested_symbols)

    def discover(self, underlying):
        if underlying not in {'SPY','QQQ'}:
            raise ValueError('Only SPY/QQQ option discovery permitted')
        return self._read('CONTRACTS_'+underlying,lambda sdk: sdk.instrument.get_option_contracts(
            category='US_OPTION',underlying_symbols=underlying,status='LISTING',page_size=10))

    def snapshots(self, symbols):
        if (not isinstance(symbols,tuple) or not 1 <= len(symbols) <= 20
                or len(set(symbols)) != len(symbols) or not all(supported_symbol(s) for s in symbols)):
            raise ValueError('One to twenty unique SPY/QQQ OCC contracts required')
        return self._read('OPTION_SNAPSHOTS',lambda sdk: sdk.option_market_data.get_option_snapshot(
            symbols=list(symbols),category='US_OPTION'),requested_symbols=symbols)

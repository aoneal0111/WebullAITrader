"""Bounded read-only futures SDK schema checks; no quote adapter or orders."""
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from app.webull.options_data_probe import OptionsDataProbe, SENSITIVE

CONTRACT = re.compile(r'(MES|MNQ)[FGHJKMNQUVXZ][0-9]{1,2}\Z')
NUMBERS = frozenset({'bid','ask','bid_price','ask_price','price','open','high','low','close',
    'volume','open_interest','multiplier','contract_multiplier','tick_size','tick_value',
    'initial_margin','maintenance_margin','margin','bid_size','ask_size'})
TIMES = frozenset({'time','timestamp','quote_time','bid_time','ask_time','bid_timestamp',
    'ask_timestamp','trade_time','expire_date','expiration_date','last_trading_date'})


def supported_contract(value):
    return isinstance(value,str) and CONTRACT.fullmatch(value) is not None


def schema_sample(value):
    remaining,symbols = [512],set()
    def visit(v,depth=0,field=''):
        remaining[0]-=1
        if remaining[0]<0 or depth>6: return {'truncated':True}
        if isinstance(v,dict):
            return {k:visit(x,depth+1,k) for k,x in list(v.items())[:64]
                if isinstance(k,str) and re.fullmatch(r'[a-z][a-z0-9_]{0,39}',k) and not SENSITIVE.search(k)}
        if isinstance(v,list):
            return {'count':len(v),'sample':[visit(x,depth+1,field) for x in v[:2]]}
        if field in {'symbol','futures_symbol','contract_symbol'} and supported_contract(v):
            symbols.add(v); return v
        if field in {'code','product_code'} and v in {'MES','MNQ'}: return v
        if field in {'contract_type','category'} and v in {'MONTHLY','US_FUTURES'}: return v
        if field in NUMBERS and isinstance(v,(str,int,float)) and not isinstance(v,bool):
            try:
                d=Decimal(str(v))
                if d.is_finite() and len(str(v))<=40: return str(v)
            except InvalidOperation: pass
        if field in TIMES and isinstance(v,str) and len(v)<=40:
            try:
                if v.isdigit() and 8<=len(v)<=16: return v
                datetime.fromisoformat(v.replace('Z','+00:00')); return v
            except ValueError: pass
        return {'type':type(v).__name__}
    return visit(value),tuple(sorted(symbols))


class FuturesDataProbe(OptionsDataProbe):
    """Reuse the bounded transport/error policy, with futures-only operations."""
    def __init__(self,factory,*,clock):
        super().__init__(factory,clock=clock,sampler=schema_sample)

    def discover(self,product):
        if product not in {'MES','MNQ'}: raise ValueError('Only MES/MNQ discovery supported')
        observed=self._read('FUTURES_CONTRACTS_'+product,lambda sdk:
            sdk.instrument.get_futures_instrument_by_code(code=product,category='US_FUTURES',contract_type='MONTHLY'))
        return replace(observed,symbols=tuple(s for s in observed.symbols if CONTRACT.fullmatch(s)[1]==product))

    def profile(self,symbol):
        if not supported_contract(symbol): raise ValueError('Actual MES/MNQ expiry contract required')
        return self._read('FUTURES_PROFILE',lambda sdk:sdk.instrument.get_futures_instrument(
            symbols=symbol,category='US_FUTURES'),requested_symbols=(symbol,))

    def depth(self,symbol):
        if not supported_contract(symbol): raise ValueError('Actual MES/MNQ expiry contract required')
        return self._read('FUTURES_DEPTH',lambda sdk:sdk.futures_market_data.get_futures_depth(
            symbol=symbol,category='US_FUTURES',depth=1),requested_symbols=(symbol,))

    def bars(self,symbol):
        if not supported_contract(symbol): raise ValueError('Actual MES/MNQ expiry contract required')
        return self._read('FUTURES_MINUTE_BARS',lambda sdk:sdk.futures_market_data.get_futures_history_bars(
            symbols=symbol,category='US_FUTURES',timespan='M1',count='48',real_time_required=True),requested_symbols=(symbol,))

    def snapshots(self,*args):
        raise ValueError('Use the explicit bounded futures profile/depth/bars checks')

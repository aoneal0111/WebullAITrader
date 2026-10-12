"""Bounded pure contract-worker field decoders; no ledger or execution authority."""
from datetime import datetime
from decimal import Decimal as D, InvalidOperation
from app.asset_modules.engine_worker import _SYMBOL


def _fields(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError('Unexpected contract transport fields')
    return value


def _decimal(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError('Bounded Decimal string required')
    try:
        v = D(value)
    except InvalidOperation as error:
        raise ValueError('Invalid Decimal string') from error
    if not v.is_finite() or v < 0 or v.adjusted() > 12 or v.as_tuple().exponent < -8:
        raise ValueError('Unsupported monetary range')
    return v


def _time(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 40:
        raise ValueError('Bounded timestamp string required')
    at = datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Aware timestamp required')
    return at


def _symbol(value):
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        raise ValueError('Canonical contract symbol required')
    return value



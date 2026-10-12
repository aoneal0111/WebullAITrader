"""Shared Decimal-preserving contract plan persistence; no trading authority."""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal as D
import json

from app.asset_modules.contract_logic import Contract, Plan
from app.asset_modules.engine_catalog import EngineId


def number(v, *, positive=False):
    if not isinstance(v, D) or not v.is_finite() or v < 0 or positive and v == 0:
        raise ValueError('Finite nonnegative Decimal required')
    return v


def encode(p):
    data = asdict(p)
    data['contract']['engine'] = p.contract.engine.value
    for key in ('created_at', 'deadline'):
        data[key] = data[key].isoformat()
    data['contract']['expires_at'] = p.contract.expires_at.isoformat()
    return json.dumps(data, default=str, sort_keys=True, separators=(',', ':'))


def decode(text):
    data = json.loads(text)
    c = data.pop('contract')
    c['engine'] = EngineId(c['engine'])
    c['expires_at'] = datetime.fromisoformat(c['expires_at'])
    for key in ('multiplier', 'tick', 'margin', 'strike'):
        c[key] = D(c[key]) if c[key] is not None else None
    for key in ('entry', 'stop', 'target', 'fee_per_side'):
        data[key] = D(data[key])
    for key in ('created_at', 'deadline'):
        data[key] = datetime.fromisoformat(data[key])
    return Plan(contract=Contract(**c), **data)


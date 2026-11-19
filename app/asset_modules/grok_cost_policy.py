"""Opt-in, durable estimated request reservations; never an invoice guarantee."""
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
from uuid import uuid4

from .grok import GrokUnavailable

FILENAME='grok-cost-policy.json'
SCOPE='WARRIOR_GROK_COST_POLICY_V1'
# Global Responses endpoint, verified 2026-11-19, uncached short-context rates.
# https://docs.x.ai/developers/pricing
PRICES={'grok-4.5':(2,6),'grok-4.6':(2,6),'grok-4.7':(2,6)}


def policy(daily_usd='1.00'):
    value=Decimal(str(daily_usd))
    if not value.is_finite() or not Decimal('0.10')<=value<=100 or value*100!=int(value*100):
        raise ValueError('DAILY_USD_CENTS_BETWEEN_010_AND_100_REQUIRED')
    return dict(scope=SCOPE,daily_micro_usd=int(value*1000000),director_percent=25,
        minimum_batch_seconds=60,minimum_symbol_seconds=300,heartbeat_seconds=1800,
        material_price_basis_points=50,day_timezone='UTC',pricing_version='GLOBAL_TEXT_20261119')


def load(root):
    path=Path(root)/FILENAME
    if path.is_symlink():raise ValueError('REGULAR_BOUNDED_GROK_COST_POLICY_REQUIRED')
    if not path.exists():return None
    if not path.is_file() or path.stat().st_size>4096:
        raise ValueError('REGULAR_BOUNDED_GROK_COST_POLICY_REQUIRED')
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict) or type(value.get('daily_micro_usd')) is not int:
        raise ValueError('VALID_GROK_COST_POLICY_REQUIRED')
    if value!=policy(Decimal(value['daily_micro_usd'])/1000000):
        raise ValueError('VALID_GROK_COST_POLICY_REQUIRED')
    return value


def estimate(model,instruction,context,schema,maximum_output_tokens):
    if model not in PRICES or type(maximum_output_tokens) is not int or not 1<=maximum_output_tokens<=2048:
        raise GrokUnavailable('GROK_COST_PRICING_UNAVAILABLE')
    # UTF-8 byte count plus a deliberately generous framing allowance, rather
    # than the usual chars/4 approximation. Cache discounts are not assumed.
    size=len(json.dumps([instruction,context,schema],allow_nan=False).encode())+8192
    if size>128000:raise GrokUnavailable('GROK_COST_CONTEXT_BOUND')
    input_rate,output_rate=PRICES[model]
    return dict(model=model,reserved_micro_usd=size*input_rate+maximum_output_tokens*output_rate)


def reserve(db,configuration,identifier,stage,estimate,at):
    if not isinstance(estimate,dict) or estimate.get('model') not in PRICES or type(estimate.get('reserved_micro_usd')) is not int:
        raise GrokUnavailable('GROK_COST_RESERVATION_REQUIRED')
    amount=estimate['reserved_micro_usd']
    if not 0<amount<=300000:raise GrokUnavailable('GROK_COST_RESERVATION_REQUIRED')
    day=at.astimezone(UTC).date().isoformat()
    total,director=db.execute("SELECT coalesce(sum(micro_usd),0),coalesce(sum(CASE WHEN stage='DIRECTOR' THEN micro_usd ELSE 0 END),0) FROM cost_reservations WHERE day=?",(day,)).fetchone()
    limit=configuration['daily_micro_usd']
    if total+amount>limit:raise GrokUnavailable('GROK_DAILY_COST_BUDGET')
    if stage=='DIRECTOR' and director+amount>limit*configuration['director_percent']//100:
        raise GrokUnavailable('GROK_DIRECTOR_COST_BUDGET')
    db.execute('INSERT INTO cost_reservations VALUES (?,?,?,?,?)',(identifier,day,stage,amount,estimate['model']))


def report(db,configuration,at):
    if configuration is None:return {'state':'NOT_CONFIGURED'}
    day=at.astimezone(UTC).date().isoformat()
    total,director,count=db.execute("SELECT coalesce(sum(micro_usd),0),coalesce(sum(CASE WHEN stage='DIRECTOR' THEN micro_usd ELSE 0 END),0),count(*) FROM cost_reservations WHERE day=?",(day,)).fetchone()
    limit=configuration['daily_micro_usd']
    return dict(state='DAILY_BUDGET_REACHED' if total>=limit else 'TRACKING',day=day,day_timezone='UTC',
        daily_budget_usd=str(Decimal(limit)/1000000),reserved_estimate_usd=str(Decimal(total)/1000000),
        remaining_estimate_usd=str(Decimal(max(0,limit-total))/1000000),
        director_reserved_estimate_usd=str(Decimal(director)/1000000),director_percent=configuration['director_percent'],
        requests_reserved_today=count,policy=dict(configuration),
        accounting='Full conservative reservation retained, including failures and unknown outcomes; not provider billing. Prior untracked calls excluded.',
        provider_account_cap_enforced=False)


def install(root,daily_usd):
    from app.equity_overhaul.paper_experiment import load as load_operation, CONTINUOUS_SCOPE
    root=Path(root).resolve()
    if load_operation(root/'experiment.json',allow_expired=True)[1]['scope']!=CONTINUOUS_SCOPE:
        raise ValueError('CONTINUOUS_PAPER_REQUIRED')
    value=policy(daily_usd);path=root/FILENAME
    if path.exists():
        previous=load(root)
        if previous==value:return value
        path.with_name('grok-cost-policy-backup-'+uuid4().hex+'.json').write_bytes(path.read_bytes())
    temp=path.with_name('grok-cost-policy-'+uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value,indent=2),encoding='utf-8');temp.replace(path)
    return value


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--paper',required=True);p.add_argument('--daily-usd',required=True)
    a=p.parse_args();print(json.dumps(install(a.paper,a.daily_usd),indent=2))

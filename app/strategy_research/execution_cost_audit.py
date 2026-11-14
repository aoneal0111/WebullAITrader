"""Bounded read-only PAPER fill/decision-quote audit using only the stdlib.

Decision-to-fill differences include market movement. They are not measured
live slippage, and this report cannot set costs_verified or authorize trading.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal as D, DecimalException
import argparse
import json
from pathlib import Path
import re
import sqlite3

from app.read_only_sqlite import bounded_rows, read_database

_SYMBOL = re.compile(r'^[A-Z0-9][A-Z0-9.-]{0,31}$')
_ENGINES = {'QUICK_SCALPER','WARRIOR_MOMENTUM_V1'}


def number(value, *, positive=False):
    if not isinstance(value,str) or not 1 <= len(value) <= 64:
        raise ValueError('INVALID_NUMBER')
    n=D(value)
    if (not n.is_finite() or not 0 <= n <= D('1e12') or (positive and n==0)
            or not -12 <= n.as_tuple().exponent <= 12):
        raise ValueError('INVALID_NUMBER')
    return n


def instant(value):
    if not isinstance(value,str) or len(value)>64:
        raise ValueError('INVALID_TIMESTAMP')
    at=datetime.fromisoformat(value)
    if at.utcoffset() is None:
        raise ValueError('INVALID_TIMESTAMP')
    return at


def audit_costs(path, *, max_quote_age_ms=2000, max_fill_delay_ms=5000):
    for v in (max_quote_age_ms,max_fill_delay_ms):
        if type(v) is not int or not 1 <= v <= 60000:
            raise ValueError('INVALID_TIME_BOUND')
    with read_database(path) as db:
        db.execute('BEGIN')
        environment=db.execute("SELECT value FROM metadata WHERE key='environment'").fetchone()
        if environment != ('PAPER',):
            raise ValueError('PAPER_ENVIRONMENT_REQUIRED')
        active=db.execute("SELECT value FROM metadata WHERE key='active_campaign_id'").fetchone()
        if not active or not isinstance(active[0],str) or not 1 <= len(active[0]) <= 128:
            raise ValueError('ACTIVE_CAMPAIGN_REQUIRED')
        rows=bounded_rows(db,"""SELECT payload FROM orders
            WHERE json_extract(payload,'$.paper_campaign_id')=?
            ORDER BY order_id LIMIT 5001""",(active[0],),5000,32_000_000)
    records=[];reasons=Counter();seen=set()
    fill_count=0;matched_quantity=D(0);reference_notional=D(0)
    signed_difference=D(0);matched_fees=D(0);all_fees=D(0)
    for order_index,(raw,) in enumerate(rows):
        order=json.loads(raw)
        request=order['request'];meta=request.get('metadata') or {}
        fills=order['fills']
        if not isinstance(fills,list) or len(fills)>1000 or not isinstance(meta,dict):
            raise ValueError('INVALID_ORDER_SHAPE')
        total=D(0)
        for fill_index,fill in enumerate(fills):
            identity=fill['fill_id']
            if not isinstance(identity,str) or not 1 <= len(identity)<=256 or identity in seen:
                raise ValueError('INVALID_OR_DUPLICATE_FILL')
            seen.add(identity)
            if fill.get('order_id') != order['order_id']:
                raise ValueError('FILL_ORDER_MISMATCH')
            qty=number(fill['quantity'],positive=True);price=number(fill['price'],positive=True)
            fee=number(fill['commission']);filled=instant(fill['timestamp'])
            total+=qty;all_fees+=fee;fill_count+=1
            if fill_count>10000:
                raise ValueError('FILL_LIMIT')
            symbol=request.get('symbol');side=request.get('side')
            reason=None;measure=None
            if request.get('asset_class')!='STOCK':
                reason='NOT_EQUITY'
            elif not isinstance(symbol,str) or not _SYMBOL.fullmatch(symbol):
                reason='INVALID_SYMBOL'
            elif side!='BUY' or meta.get('strategy_owner')!='QUICK_SCALPER':
                reason='NO_SUPPORTED_DECISION_QUOTE'
            else:
                try:
                    # The scalper constructor anchors entry_trigger to the observed
                    # ask. It is a decision reference, never a fill-time quote.
                    bid=number(meta['scalp_execution_bid'],positive=True)
                    ask=number(meta['scalp_entry_trigger'],positive=True)
                    decision=instant(meta['scalp_execution_quote_timestamp'])
                    bid_at=instant(meta['scalp_provider_bid_timestamp'])
                    ask_at=instant(meta['scalp_provider_ask_timestamp'])
                    created=instant(order['created_at'])
                    if bid>ask:
                        reason='CROSSED_DECISION_QUOTE'
                    elif not bid_at<=decision or not ask_at<=decision or not decision<=created<=filled:
                        reason='INVALID_TIME_ORDER'
                    elif max((decision-bid_at).total_seconds(),(decision-ask_at).total_seconds())*1000>max_quote_age_ms:
                        reason='STALE_AT_DECISION'
                    elif (filled-decision).total_seconds()*1000>max_fill_delay_ms:
                        reason='DECISION_FILL_DELAY_EXCEEDED'
                    else:
                        mid=(bid+ask)/2
                        delta=(price-mid)*qty
                        reference_notional+=mid*qty;signed_difference+=delta
                        matched_quantity+=qty;matched_fees+=fee
                        measure=dict(decision_at=decision.isoformat(),order_created_at=created.isoformat(),
                            bid=bid,ask_anchor=ask,midpoint=mid,
                            decision_to_fill_ms=D(str((filled-decision).total_seconds()))*1000,
                            signed_midpoint_difference_bps=(price-mid)/mid*10000,
                            signed_ask_difference_per_share=price-ask,
                            half_spread_bps=(ask-bid)/2/mid*10000)
                except (KeyError,TypeError,ValueError,DecimalException,OverflowError):
                    reason='MISSING_OR_INVALID_DECISION_QUOTE'
            if reason:reasons[reason]+=1
            record=dict(order_index=order_index,fill_index=fill_index,
                symbol=symbol if isinstance(symbol,str) and _SYMBOL.fullmatch(symbol) else 'UNKNOWN',
                engine=meta.get('strategy_owner') if meta.get('strategy_owner') in _ENGINES else 'UNKNOWN',
                side=side if side in {'BUY','SELL'} else 'UNKNOWN',quantity=qty,price=price,
                commission=fee,filled_at=filled.isoformat(),decision_reference=measure,exclusion=reason)
            records.append(record)
        if total!=number(order['filled_quantity']):
            raise ValueError('FILLED_QUANTITY_MISMATCH')
    matched=sum(r['decision_reference'] is not None for r in records)
    return dict(schema_version='EXECUTION_COST_AUDIT_V1',scope='ACTIVE_CAMPAIGN_DURABLE_PAPER_FILLS',
        status='INSUFFICIENT_LIVE_EXECUTION_EVIDENCE',execution_authorized=False,costs_verified=False,
        orders=0,network_requests=0,source_orders=len(rows),fills=fill_count,
        decision_reference_matches=matched,unmatched_fills=fill_count-matched,exclusions=dict(reasons),
        total_recorded_commission=all_fees,matched_quantity=matched_quantity,
        matched_reference_notional=reference_notional,
        matched_signed_midpoint_difference_bps=signed_difference/reference_notional*10000 if reference_notional else None,
        matched_commission_bps=matched_fees/reference_notional*10000 if reference_notional else None,
        max_quote_age_ms=max_quote_age_ms,max_fill_delay_ms=max_fill_delay_ms,records=records,
        required_next_evidence=['Observed bid/ask with quote availability time for each BUY and SELL fill.',
            'Broker-confirmed fills, side, quantity, commission and order submission/acknowledgment times.',
            'Verified live versus simulated provenance and representative sessions/symbols.'],
        limitations=['The durable source is PAPER; matches do not verify live costs.',
            'Only QUICK_SCALPER BUY decision references are supported; SELL quotes are not inferred.',
            'scalp_entry_trigger is the original observed ask anchor, not a replacement limit or fill-time quote.',
            'Decision-to-fill price differences include market movement; negative values are retained.',
            'No fees, quotes or timestamps are invented for missing evidence; exclusions remain visible.',
            'Reported fee totals use recorded commission only; missing exchange or regulatory charges remain unverified.',
            'This report cannot set the research review costs_verified flag or change strategy parameters.'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--max-quote-age-ms',type=int,default=2000)
    parser.add_argument('--max-fill-delay-ms',type=int,default=5000)
    args=parser.parse_args(argv)
    try:
        report=audit_costs(args.ledger,max_quote_age_ms=args.max_quote_age_ms,
                           max_fill_delay_ms=args.max_fill_delay_ms)
        print(json.dumps(report,default=str,indent=2,allow_nan=False))
        return 0
    except (ValueError,TypeError,KeyError,OSError,sqlite3.Error,DecimalException,OverflowError):
        parser.exit(2,'EXECUTION_COST_AUDIT_FAILED; no report was produced.\n')


if __name__=='__main__':raise SystemExit(main())

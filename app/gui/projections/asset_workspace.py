"""Read-only market projections for the shared workstation widgets."""
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.gui.models import (ActivityEntry, ActivitySnapshot, PositionsSnapshot, PositionManagementRow,
    OrdersSnapshot, PortfolioDashboardSnapshot, WatchlistSnapshot)
from app.read_models.orders import OrderReadModel, OrdersReadModelSnapshot


def text(value):
    if value is None:return '--'
    return str(value).replace('<','‹').replace('>','›')[:300]


def number(value):
    try:
        result=Decimal(str(value))
        return result if result.is_finite() and abs(result.adjusted())<=24 and result.as_tuple().exponent>=-18 else None
    except (InvalidOperation,ValueError,TypeError):return None


def money(value, *, signed=False):
    n=number(value)
    return '--' if n is None else ('-$'+f'{abs(n):,.2f}' if n<0 else ('+' if signed and n>0 else '')+'$'+f'{n:,.2f}')


def price(value, *, currency=True):
    n=number(value)
    if n is None:return '--'
    places=max(2,-n.as_tuple().exponent)
    whole,fraction=f'{n:,.{places}f}'.rsplit('.',1)
    fraction=fraction.rstrip('0').ljust(2,'0')
    return ('$' if currency else '')+whole+'.'+fraction


def stamp(value):
    try:
        result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result if result.tzinfo else result.replace(tzinfo=UTC)
    except (ValueError,TypeError):return None


@dataclass(frozen=True)
class MarketView:
    notice:str
    state:str='UNAVAILABLE'
    feed:str='UNAVAILABLE'
    broker:str='UNAVAILABLE'
    portfolio:PortfolioDashboardSnapshot=PortfolioDashboardSnapshot((),())
    positions:PositionsSnapshot=PositionsSnapshot(())
    orders:OrdersSnapshot=OrdersSnapshot(())
    order_models:OrdersReadModelSnapshot=OrdersReadModelSnapshot()
    activity:ActivitySnapshot=ActivitySnapshot(())
    scanner:WatchlistSnapshot=WatchlistSnapshot(empty_title='Scanner unavailable',
        empty_detail='No market quote source attached.',scanner_status='NOT CONNECTED')


def portfolio(*,cash=None,realized=None,positions=None,orders=None):
    # No marks/account balance feed: never substitute cost for equity or
    # margin headroom for broker buying power; unknowns remain unknown.
    return PortfolioDashboardSnapshot(tuple((k,v) for k,v in (
        ('Equity','--'),('Cash',money(cash)),('Buying Power','--'),
        ('Unrealized P/L','--'),('Realized P/L',money(realized,signed=True)),
        ('Open Positions',text(positions)),('Exposure','--'),('Working Orders',text(orders)))),())


def crypto_view(bot,checked_at):
    if bot is None:return MarketView('No prepared crypto paper wallet found.',
        scanner=WatchlistSnapshot(empty_title='No crypto scanner detail',empty_detail='No wallet selected.',scanner_status='UNAVAILABLE'))
    label=text(bot.get('label'));health=text(bot.get('health'));container=text(bot.get('container_state'))
    live=not bot.get('read_error') and bot.get('container_running') is True and health=='RECENT_CHECKPOINT' and bot.get('state')=='RUNNING'
    state=('FAILED' if bot.get('read_error') else 'RUNNING' if live else 'FAILED' if health in {'READ_ERROR','CAPTURE_ERROR','FAILED'}
           else 'STOPPED' if bot.get('container_running') is False or bot.get('state')=='STOPPED' else 'UNVERIFIED')
    notice=(f'{label} · PAPER · {health} · Bot {container}. '
        'Selected wallet only. Current balances and unrealized P/L unavailable. '+
        ('Entries paused.' if bot.get('pause_new_entries') is True else
         'Entries not paused.' if bot.get('pause_new_entries') is False else 'Entry status unknown.'))
    if bot.get('read_error'):notice+=' Read error: '+text(bot['read_error'])
    available=bot.get('ledger_present') is True and not bot.get('read_error')
    holdings=bot.get('holdings',[]) if available else []
    rows=[];management=[]
    for h in holdings:
        symbol=text(h.get('pair'));qty=text(h.get('amount'));entry=price(h.get('open_rate'));when=text(h.get('open_date'))
        rows.append((symbol,'LONG',qty,entry,'--','--','--',money(h.get('realized_profit')),when,money(h.get('stake_amount')),'--'))
        management.append(PositionManagementRow(symbol,'LONG',qty,entry,'--','--','--',
            money(h.get('realized_profit')),when,entry_notional=money(h.get('stake_amount')),
            current_stop=price(h.get('stop_loss')),strategy=label,setup=text(h.get('enter_tag')),
            management_state='Native worker ledger · exchange protection not evidenced'))
    closed=[];events=[]
    for h in bot.get('closed',[]) if available else []:
        when=text(h.get('close_date'));symbol=text(h.get('pair'))
        closed.append((symbol,'LONG',text(h.get('amount')),price(h.get('open_rate')),price(h.get('close_rate')),
            '--','--',money(h.get('close_profit_abs')),when,'--','--'))
        at=stamp(h.get('close_date'))
        if at:events.append(ActivityEntry(at,f"Closed · Net {money(h.get('close_profit_abs'))} · {text(h.get('exit_reason'))}",
            'PAPER','INFO',label,symbol))
    order_rows=[];models=[];updates=[]
    for i,o in enumerate(bot.get('orders',[]) if available else []):
        symbol=text(o.get('symbol'));side=text(o.get('ft_order_side')).upper();kind=text(o.get('order_type')).upper()
        at=stamp(o.get('order_date'))
        row=(symbol,side,kind,text(o.get('amount')),text(o.get('filled')),text(o.get('remaining')),
            text(o.get('price')),'--','--','WORKING')
        order_rows.append(row);updates.append(text(o.get('order_date')))
        # Do not manufacture an order event timestamp from a GUI refresh.
        if at:models.append(OrderReadModel(text(o.get('order_id',i)),symbol,side,text(o.get('amount')),
            'WORKING',at,order_type=kind,limit_price=None if o.get('price') is None else text(o['price']),
            filled_quantity=text(o.get('filled')),remaining_quantity=text(o.get('remaining')),execution_source=label))
    realized=None
    if available:
        closed_net=number(bot.get('closed_lifecycle_net_usd'))
        partial_net=number(bot.get('open_lifecycle_realized_usd'))
        if partial_net is None and bot.get('open_positions')==0:partial_net=Decimal(0)
        if closed_net is not None and partial_net is not None:realized=closed_net+partial_net
    conditions=', '.join(f'{text(k)}: {text(v)}' for k,v in (bot.get('source_states') or {}).items())
    return MarketView(notice,state,'RECENT CANDLES' if live else health,'PAPER SIMULATOR',
        portfolio(realized=realized,
            positions=bot.get('open_positions') if available else None,orders=bot.get('working_orders') if available else None),
        PositionsSnapshot(tuple(rows),tuple(management),tuple(closed)),OrdersSnapshot(tuple(order_rows),updated_at=tuple(updates)),
        OrdersReadModelSnapshot(tuple(models)),ActivitySnapshot(tuple(events)),
        WatchlistSnapshot(empty_title='Pair-level quotes unavailable',
            empty_detail=f"Fresh pairs: {text(bot.get('fresh_ready_pairs'))}. {conditions or 'No source-condition snapshot.'}",
            scanner_status=health,candidate_count=0))


def derivative_view(view,asset):
    available=view.status=='AVAILABLE'
    notice=f'{asset.title()} · Saved PAPER ledger · {text(view.notice)} · No market feed or trading worker connected.'
    rows=[];management=[];events=[];closed=[]
    for p in view.positions if available else ():
        symbol=text(p.get('contract'));side=text(p.get('side'));qty=text(p.get('quantity'));entry=money(p.get('entry'))
        rows.append((symbol,side,qty,entry,'--','--','--','--',text(p.get('expiry')),'--','--'))
        management.append(PositionManagementRow(symbol,side,qty,entry,'--','--','--','--',text(view.read_at),
            current_stop=money(p.get('stop')),next_target=money(p.get('target')),
            management_state='Saved simulator plan · not a working broker order',strategy=asset.title()))
    for e in view.events if available else ():
        at=stamp(e.get('time'))
        if e.get('action')=='CLOSE':
            closed.append((text(e.get('contract')),text(e.get('side')),text(e.get('quantity')),
                '--',money(e.get('price')),'--','--',money(e.get('net_pnl')),text(e.get('time')),'--','--'))
        if at:events.append(ActivityEntry(at,f"{text(e.get('action'))} · {text(e.get('quantity'))} contracts · {money(e.get('price'))} · Net {money(e.get('net_pnl'))}",
            'SAVED PAPER','INFO',asset.title(),text(e.get('contract'))))
    return MarketView(notice,portfolio=portfolio(cash=view.cash if available else None,
        realized=view.realized_pnl if available else None,positions=len(view.positions) if available else None),
        positions=PositionsSnapshot(tuple(rows),tuple(management),tuple(closed)),activity=ActivitySnapshot(tuple(events)),
        scanner=WatchlistSnapshot(empty_title=f'{asset.title()} feed unavailable',
            empty_detail='Saved positions do not establish scanner quotes or active trading.',scanner_status='NOT CONNECTED'))

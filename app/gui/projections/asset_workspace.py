"""Read-only market projections for the shared workstation widgets."""
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.gui.models import (ActivityEntry, ActivitySnapshot, PositionsSnapshot, PositionManagementRow,
    OrdersSnapshot, PortfolioDashboardSnapshot, WatchlistSnapshot, WatchlistRow)
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
    telemetry=bot.get('workspace_telemetry') or {}
    if not live:telemetry={}
    positions={p.get('id'):p for p in telemetry.get('positions',[])}
    rows=[];management=[]
    inventory_coherent=(bool(telemetry) and bot.get('open_positions')==len(holdings)==len(positions)
        and all(number(h.get('amount')) is not None
            and number(positions.get(h.get('id'),{}).get('amount'))==number(h.get('amount'))
            and positions.get(h.get('id'),{}).get('pair')==h.get('pair') for h in holdings))
    marks_complete=inventory_coherent
    exposure=Decimal(0);unrealized=Decimal(0)
    for h in holdings:
        symbol=text(h.get('pair'));qty=text(h.get('amount'));entry=price(h.get('open_rate'));when=text(h.get('open_date'))
        valued=positions.get(h.get('id'),{})
        coherent=(valued.get('pair')==h.get('pair') and number(valued.get('amount'))==number(h.get('amount')))
        if valued.get('mark_basis')=='NATIVE BID':
            at=stamp(valued.get('mark_at'));checked=stamp(checked_at)
            coherent=coherent and at is not None and checked is not None and 0<=(checked-at).total_seconds()<=60
        if not coherent:valued={}
        mark=number(valued.get('mark'));mv=number(valued.get('market_value'));up=number(valued.get('unrealized'))
        if mark is None or mv is None or up is None:marks_complete=False
        else:exposure+=mv;unrealized+=up
        pct=number(valued.get('profit_ratio'))
        pct='--' if pct is None else f'{pct*100:+.2f}%'
        basis=text(valued.get('mark_basis')) if mark is not None else 'MARK UNAVAILABLE'
        when=text(valued.get('mark_at')) if mark is not None else when
        rows.append((symbol,'LONG',qty,entry,price(mark),money(up,signed=True),pct,money(h.get('realized_profit')),when,money(h.get('stake_amount')),money(mv)))
        management.append(PositionManagementRow(symbol,'LONG',qty,entry,price(mark),money(up,signed=True),pct,
            money(h.get('realized_profit')),when,entry_notional=money(h.get('stake_amount')),market_value=money(mv),
            current_stop=price(h.get('stop_loss')),strategy=label,setup=text(h.get('enter_tag')),
            management_state='PAPER · '+('NATIVE BID' if basis=='NATIVE BID' else 'CANDLE ESTIMATE' if mark is not None else 'MARK UNAVAILABLE')))
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
    markets=telemetry.get('markets',{})
    scanner_rows=[]
    for pair,market in markets.items():
        source=market.get('source') or {};quote=market.get('quote') or {}
        observed=stamp(quote.get('observed_at'));checked=stamp(checked_at)
        fresh=observed is not None and checked is not None and 0<=(checked-observed).total_seconds()<=60
        last=quote.get('last') if fresh else market.get('candle_close') if market.get('ready') else None
        basis='NATIVE QUOTE' if fresh else 'CANDLE '+text(market.get('timeframe')) if last is not None else 'STALE / UNAVAILABLE'
        inputs=source.get('entry_inputs') or {};reason=text(source.get('state'))
        scanner_rows.append(WatchlistRow(symbol=pair,selected=False,rank=str(len(scanner_rows)+1),latest_price=price(last),
            change='--',change_percent=text(quote.get('percentage'))+'%' if fresh and quote.get('percentage') is not None else '--',
            bid=price(quote.get('bid')) if fresh else '--',ask=price(quote.get('ask')) if fresh else '--',
            volume=text(quote.get('baseVolume') if fresh else market.get('candle_volume')),
            market_status=reason,last_update=text(quote.get('observed_at') if fresh else market.get('candle_closed_at',market.get('candle_at'))),
            stale=basis,freshness=basis,session='24/7',classification=reason if market.get('ready') else 'STALE',setup=text(source.get('enter_tag')),
            strategy_name=label,strategy_status=reason,explanations=reason+' · Indicators: '+text(inputs)+' · Parameters: '+text(source.get('entry_parameters') or {}),
            blocking_reasons='--' if source.get('buy')==1 else reason))
    metrics=dict(portfolio(realized=realized,positions=bot.get('open_positions') if available else None,
        orders=bot.get('working_orders') if available else None).metrics)
    account=telemetry.get('account') or {}
    if inventory_coherent and account.get('currency')=='USD':
        metrics['Cash']=money(account.get('total'));metrics['Buying Power']=money(account.get('available_stake'))
        if marks_complete:
            cash=number(account.get('total'))
            metrics['Equity']=money(cash+exposure) if cash is not None else '--'
    if marks_complete:
        metrics['Exposure']=money(exposure);metrics['Unrealized P/L']=money(unrealized,signed=True)
    if telemetry:
        notice=(f'{label} · PAPER · {health}. Selected wallet. Native balances; marks may be completed-candle estimates. '
            'Stop retains holdings and stops exit management. '+('Entries paused.' if bot.get('pause_new_entries') else 'Entries not paused.'))
    else:notice+=' Worker display telemetry: '+text(bot.get('workspace_telemetry_state','UNAVAILABLE'))+'.'
    return MarketView(notice,state,'WORKER MARKS' if telemetry else 'RECENT CANDLES' if live else health,'PAPER SIMULATOR',
        PortfolioDashboardSnapshot(tuple(metrics.items()),()),
        PositionsSnapshot(tuple(rows),tuple(management),tuple(closed)),OrdersSnapshot(tuple(order_rows),updated_at=tuple(updates)),
        OrdersReadModelSnapshot(tuple(models)),ActivitySnapshot(tuple(events)),
        WatchlistSnapshot(rows=tuple(scanner_rows),empty_title='Worker display telemetry unavailable',
            empty_detail=f"Fresh pairs: {text(bot.get('fresh_ready_pairs'))}. {conditions or 'No source-condition snapshot.'}",
            scanner_status=health,candidate_count=len(scanner_rows)))



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

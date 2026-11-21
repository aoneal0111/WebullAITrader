from decimal import Decimal as D
from types import SimpleNamespace as NS
from app.paper_trading.owned_quantity import scalper_open_quantity

ID='QUICK_SCALPER|ABC|owned'


def order(identity,qty,side='BUY',owner='QUICK_SCALPER'):
    return NS(symbol='ABC',filled_quantity=D(qty),request=NS(strategy_lifecycle_id=identity,
        side=NS(value=side),metadata={'strategy_owner':owner}))


def test_closed_history_does_not_hide_current_partial_lifecycle_and_no_shorts_are_invented():
    rows=[order('historical','100'),order('historical','100','SELL'),order(ID,'18')]
    assert scalper_open_quantity(rows,'ABC',ID)==18
    rows.append(order(ID,'57'));assert scalper_open_quantity(rows,'ABC',ID)==75
    rows.append(order(ID,'75','SELL'));assert scalper_open_quantity(rows,'ABC',ID)==0


def test_ambiguous_owner_multiple_live_contributors_and_wrong_lifecycle_fail_closed():
    assert scalper_open_quantity([order(ID,'18',owner='OTHER')],'ABC',ID)==0
    assert scalper_open_quantity([order(ID,'18'),order('unidentified','1')],'ABC',ID)==0
    assert scalper_open_quantity([order(ID,'18')],'ABC',ID+'-different')==0
    assert scalper_open_quantity([order(ID,'18')],'ABC','WARRIOR|ABC|one') is None

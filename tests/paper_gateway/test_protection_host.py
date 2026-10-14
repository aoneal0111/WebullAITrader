from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
import multiprocessing
import sqlite3

import pytest

from app.gui.runtime_ownership import acquire_runtime_ownership
from app.market_data.models import MarketEvent, MarketEventType, ResumePayload, TradingHaltPayload
from app.paper_trading.order_models import OrderSide, OrderType
from app.paper_trading.protection_host import PaperProtectionHost
from app.paper_trading.protection_host_check import T0, seed, quote, synthetic_check, _worker
from app.trade_intelligence.knowledge.capture_profit_export import read_database


def host(tmp_path, **kwargs):
    path = tmp_path / "paper-execution.sqlite3"
    seed(path, **kwargs)
    now = [T0 + timedelta(seconds=2)]
    return PaperProtectionHost(path, environment="PAPER", clock=lambda: now[0]), now


def resume(runtime, now):
    now[0] += timedelta(seconds=1)
    return runtime.process(MarketEvent(2, now[0], "DEMO", "test", MarketEventType.RESUME, ResumePayload("test")))


def test_subprocess_takes_over_after_original_runtime_closes():
    result = synthetic_check()
    assert result["passed"] and result["separate_process"]
    assert result["duplicate_owner_blocked"] and result["positions_remaining"] == 0
    assert result["qt_widgets_imported"] is False
    assert result["network_requests"] == 0 and result["entries_enabled"] is False


def test_takeover_cancels_pending_buy_repairs_stop_and_allows_read_only_inspection(tmp_path):
    runtime, now = host(tmp_path, stop=False)
    try:
        working = runtime._commands.order_book.open_orders()
        assert len(working) == 1 and working[0].request.order_type is OrderType.STOP
        assert working[0].remaining_quantity == D("10")
        assert acquire_runtime_ownership(runtime.path.with_name("atlas-paper-runtime.lock")) is None
        with pytest.raises(RuntimeError, match="already owned"):
            PaperProtectionHost(runtime.path, environment="PAPER", clock=lambda: now[0])
        with read_database(runtime.path) as db:
            assert db.execute("SELECT COUNT(*) FROM orders").fetchone()[0] >= 2
        assert runtime.snapshot()["matching_paused_symbols"] == 1
        now[0] += timedelta(seconds=1)
        assert runtime.process(quote(now[0])) == "HALTED"
        assert runtime.snapshot()["positions"] == 1
    finally:
        runtime.close()
    lease = acquire_runtime_ownership(runtime.path.with_name("atlas-paper-runtime.lock"))
    assert lease is not None
    lease.release()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.process(quote(now[0]))


@pytest.mark.parametrize("environment", ["LIVE", "TEST", "paper"])
def test_non_paper_environment_and_missing_ledger_never_create_database(tmp_path, environment):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(ValueError):
        PaperProtectionHost(path, environment=environment)
    assert not path.exists()
    with pytest.raises(ValueError, match="Existing"):
        PaperProtectionHost(path, environment="PAPER")
    assert not path.exists()


def test_unclassified_inventory_rejected_before_source_mutation_and_lock_released(tmp_path):
    path = tmp_path / "paper-execution.sqlite3"
    seed(path, lifecycle="UNKNOWN|DEMO|test")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unclassified"):
        PaperProtectionHost(path, environment="PAPER", clock=lambda: T0)
    assert path.read_bytes() == before
    lease = acquire_runtime_ownership(path.with_name("atlas-paper-runtime.lock"))
    assert lease is not None
    lease.release()


def test_inconsistent_durable_fills_rejected_before_recovery_mutation(tmp_path):
    path = tmp_path / "paper-execution.sqlite3"
    seed(path)
    with sqlite3.connect(path) as db:
        for identity, raw in db.execute("SELECT order_id,payload FROM orders").fetchall():
            value = json.loads(raw)
            if value['request']['side'] == 'BUY':
                value['filled_quantity'] = '11'
                db.execute("UPDATE orders SET payload=? WHERE order_id=?", (json.dumps(value), identity))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="quantity disagreement"):
        PaperProtectionHost(path, environment="PAPER", clock=lambda: T0)
    assert path.read_bytes() == before


@pytest.mark.parametrize("offset,expected", [(-10,"STALE_OR_FUTURE_EVENT"),(1,"STALE_OR_FUTURE_EVENT"),(-3,"BEFORE_HOST_START")])
def test_stale_future_and_pre_start_quotes_never_fill(tmp_path, offset, expected):
    runtime, now = host(tmp_path)
    try:
        resume(runtime, now)
        now[0] += timedelta(seconds=1)
        assert runtime.process(quote(now[0]+timedelta(seconds=offset))) == expected
        assert runtime.snapshot()['positions'] == 1
    finally:
        runtime.close()


def test_partial_stop_deduplicates_quote_and_restart_does_not_reconsume_old_liquidity(tmp_path):
    runtime, now = host(tmp_path)
    try:
        resume(runtime, now)
        now[0] += timedelta(seconds=1)
        event = quote(now[0], size="4")
        assert runtime.process(event) == 'PROCESSED'
        assert runtime._quantity('DEMO') == D('6')
        assert runtime.process(event) == 'DUPLICATE_OR_OLDER_QUOTE'
        assert runtime._quantity('DEMO') == D('6')
    finally:
        runtime.close()
    now[0] += timedelta(seconds=1)
    recovered = PaperProtectionHost(runtime.path, environment='PAPER', clock=lambda: now[0])
    try:
        assert recovered.process(event) == 'BEFORE_HOST_START'
        assert recovered._quantity('DEMO') == D('6')
        resume(recovered, now)
        now[0] += timedelta(seconds=1)
        assert recovered.process(quote(now[0],size='6')) == 'PROCESSED'
        assert recovered.snapshot()['positions'] == 0
        assert not recovered._commands.order_book.open_orders()
    finally:
        recovered.close()


def test_halt_and_old_resume_cannot_allow_matching(tmp_path):
    runtime, now = host(tmp_path)
    try:
        resume(runtime, now)
        earlier = now[0]
        now[0] += timedelta(seconds=1)
        assert runtime.process(MarketEvent(3, now[0], 'DEMO','test',MarketEventType.TRADING_HALT,
                                          TradingHaltPayload('test'))) == 'HALTED'
        assert runtime.process(MarketEvent(2, earlier,'DEMO','test',MarketEventType.RESUME,
                                          ResumePayload('old'))) == 'DUPLICATE_OR_OLDER_STATUS'
        now[0] += timedelta(seconds=1)
        assert runtime.process(quote(now[0])) == 'HALTED'
        assert runtime.snapshot()['positions'] == 1
    finally:
        runtime.close()


@pytest.mark.parametrize('bid,ask,size', [('10','9','10'),('NaN','10','10'),('9','10','-1')])
def test_invalid_quote_payload_never_changes_inventory(tmp_path,bid,ask,size):
    runtime, now = host(tmp_path)
    try:
        event = quote(now[0])
        bad = replace(event, payload=replace(event.payload,bid=D(bid),ask=D(ask),bid_size=D(size)))
        with pytest.raises(ValueError):
            runtime.process(bad)
        assert runtime._quantity('DEMO') == D('10')
    finally:
        runtime.close()


@pytest.mark.parametrize('change', ['missing_stop', 'owner_conflict'])
def test_missing_stop_or_conflicting_owner_never_invents_protection(tmp_path,change):
    path = tmp_path / 'paper-execution.sqlite3'
    seed(path,stop=False)
    with sqlite3.connect(path) as db:
        for identity,raw in db.execute('SELECT order_id,payload FROM orders').fetchall():
            value = json.loads(raw)
            if value['request']['side'] != 'BUY':
                continue
            if change == 'missing_stop':
                value['request']['structural_stop_price'] = None
                value['request']['metadata'].pop('structural_stop',None)
            else:
                value['request']['metadata']['strategy_owner'] = 'QUICK_SCALPER'
            db.execute('UPDATE orders SET payload=? WHERE order_id=?',(json.dumps(value),identity))
    with pytest.raises(ValueError):
        PaperProtectionHost(path,environment='PAPER',clock=lambda:T0)
    with read_database(path) as db:
        assert db.execute("SELECT COUNT(*) FROM orders WHERE json_extract(payload,'$.request.side')='SELL'").fetchone()[0] == 0


def test_working_market_liquidation_survives_takeover_without_new_stop_or_target(tmp_path):
    runtime, now = host(tmp_path, lifecycle='QUICK_SCALPER|DEMO|test')
    life = 'QUICK_SCALPER|DEMO|test'
    try:
        result = runtime._bridge.ensure_exit('DEMO',10,D('9.99'),'SCALP_MAX_HOLD',life)
        assert result.protection_active
        identity = result.order_id
    finally:
        runtime.close()
    now[0] += timedelta(seconds=1)
    recovered = PaperProtectionHost(runtime.path,environment='PAPER',clock=lambda:now[0])
    try:
        working = recovered._commands.order_book.open_orders()
        assert len(working) == 1 and working[0].order_id == identity
        assert working[0].request.order_type is OrderType.MARKET
        resume(recovered,now)
        now[0] += timedelta(seconds=1)
        assert recovered.process(quote(now[0],size='10')) == 'PROCESSED'
        assert recovered.snapshot()['positions'] == 0
    finally:
        recovered.close()


def test_existing_profit_target_and_tightened_stop_are_preserved(tmp_path):
    runtime, now = host(tmp_path)
    life = 'WARRIOR_MOMENTUM_V1|DEMO|SYNTHETIC'
    try:
        result = runtime._bridge.ensure_exit('DEMO',10,D('10.50'),'FIRST_TARGET',life)
        assert result.protection_active
        target_id = result.order_id
    finally:
        runtime.close()
    now[0] += timedelta(seconds=1)
    recovered = PaperProtectionHost(runtime.path,environment='PAPER',clock=lambda:now[0])
    try:
        working = recovered._commands.order_book.open_orders()
        assert next(o for o in working if o.request.order_type is OrderType.LIMIT).order_id == target_id
        assert next(o for o in working if o.request.order_type is OrderType.STOP).request.stop_price == D('10')
        resume(recovered,now)
        now[0] += timedelta(seconds=1)
        assert recovered.process(quote(now[0],bid='10.50')) == 'PROCESSED'
        assert recovered.snapshot()['positions'] == 0
        assert not recovered._commands.order_book.open_orders()
    finally:
        recovered.close()


def test_process_death_releases_shared_gui_lock_without_deleting_lock_file(tmp_path):
    path = tmp_path / 'paper-execution.sqlite3'
    seed(path)
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe()
    worker = context.Process(target=_worker,args=(str(path),child))
    worker.start()
    child.close()
    try:
        assert parent.poll(15) and parent.recv().get('ready')
        worker.terminate()
        worker.join(5)
        assert not worker.is_alive()
        lock = path.with_name('atlas-paper-runtime.lock')
        assert lock.is_file()
        lease = acquire_runtime_ownership(lock)
        assert lease is not None
        lease.release()
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
        parent.close()

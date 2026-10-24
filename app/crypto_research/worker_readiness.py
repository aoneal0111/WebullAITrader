"""Disconnected synthetic worker/allocation/ledger round trip. No desktop wiring."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace

from app.asset_modules.admission_controller import PaperAdmissionController
from app.asset_modules.contract_logic import Bar
from app.asset_modules.crypto_allocation import CryptoAllocationRouter
from app.asset_modules.crypto_paper import CryptoPaper
from app.asset_modules.crypto_signals import CryptoHistory, CryptoSignalSettings, POLICY
from app.asset_modules.crypto_worker import CryptoSignalWorker, CryptoWorkerController
from app.asset_modules.engine_worker import WorkerState
from app.crypto_research.models import CryptoPair


def _wait(worker):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        reply = worker.poll()
        if reply and reply['kind'] == 'READY':
            return
        if worker.state == WorkerState.FAILED:
            break
        time.sleep(.01)
    raise RuntimeError('Synthetic worker startup failed')


def _complete(parent, quotes, now):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = parent.poll(quotes, now=now)
        if result['entry'] != 'PENDING_WORKER':
            return result['entry']
        time.sleep(.01)
    raise RuntimeError('Synthetic worker completion failed')


def _scenario(root, exit_kind):
    # Deliberately invented market and account, never loaded from .env or a feed.
    now = datetime(2026, 10, 13, 15, tzinfo=UTC)
    bars = tuple(Bar(now-timedelta(seconds=1, minutes=21-i), D('100.2'), D(98), D(100))
                 for i in range(21))
    history = CryptoHistory('BTC/USD', bars + (Bar(now-timedelta(seconds=1), D('101.2'), D(100), D(101)),))
    quote = SimpleNamespace(pair=CryptoPair('BTC', 'USD', 'BTCUSD'),
                            bid=D(101), ask=D('101.1'), timestamp=now)
    quotes = {'BTC/USD': quote}
    shared_path, ledger_path = root/'shared.sqlite3', root/'crypto.sqlite3'
    shared = PaperAdmissionController(shared_path)
    shared.configure_account('synthetic-only', capital=D(1000), loss_budget=D(50))
    ledger = CryptoPaper(ledger_path)
    worker = CryptoSignalWorker(timeout_seconds=10)
    parent = None
    try:
        router = CryptoAllocationRouter(shared, ledger, account_id='synthetic-only', authorize=lambda *a: True)
        parent = CryptoWorkerController(worker, router, settings=CryptoSignalSettings())
        worker.start()
        _wait(worker)
        separate = worker.pid != os.getpid() and worker.status()['execution_enabled'] is False

        def assess(at=now):
            submitted = parent.submit(history, quotes, now=at)['entry']
            if submitted != 'PENDING_WORKER':
                raise RuntimeError('Synthetic assessment was not accepted')
            return _complete(parent, quotes, at)

        disabled = assess()
        disabled_empty = not ledger.snapshot()['positions'] and shared.snapshot('synthetic-only')['active_commands'] == 0
        router.set_enabled(True)  # Only the new temporary simulator, no application config.
        parent.submit(history, quotes, now=now)
        expired = _complete(parent, quotes, now+timedelta(seconds=3))
        expired_empty = not ledger.snapshot()['positions'] and shared.snapshot('synthetic-only')['active_commands'] == 0
        parent.submit(history, quotes, now=now)
        router.set_enabled(False)
        revoked = _complete(parent, quotes, now)
        router.set_enabled(True)
        opened = assess()
        reserved = shared.snapshot('synthetic-only')['reserved_capital'] > 0
        duplicate = assess()
        one_position = len(ledger.snapshot()['positions']) == 1 and shared.snapshot('synthetic-only')['active_commands'] == 1
        identity = ledger.active_entry_id('BTC/USD')
        ledger_id = ledger.ledger_id
        position = ledger.snapshot()['positions'][0]
        parent.close()
        ledger.close()
        ledger = None

        # Reopen both durable stores with entries disabled and no running worker.
        shared = PaperAdmissionController(shared_path)
        ledger = CryptoPaper(ledger_path)
        router = CryptoAllocationRouter(shared, ledger, account_id='synthetic-only', authorize=lambda *a: False)
        recovered = (not router.enabled and ledger.ledger_id == ledger_id
                     and ledger.active_entry_id('BTC/USD') == identity
                     and list(router.reconcile().values()) == ['HELD'])
        quote.timestamp = now + timedelta(seconds=1)
        quote.bid = D(position['target']) + 1 if exit_kind == 'TARGET' else D(position['stop']) - 1
        quote.ask = quote.bid + D('.1')
        protection = router.protect(quotes, now=quote.timestamp)
        state = ledger.snapshot()
        evidence = ledger.intent_evidence(identity)
        settled = (not state['positions'] and shared.snapshot('synthetic-only')['active_commands'] == 0
                   and evidence['state'] == 'CLOSED' and evidence['remaining_quantity'] == 0
                   and list(protection['reconciliation'].values()) == ['CLOSED']
                   and not protection['protection'])
        events = len(state['events'])
        router.protect(quotes, now=quote.timestamp)
        idempotent = len(ledger.snapshot()['events']) == events == 2
        checks = dict(separate_worker=separate, default_entries_blocked=disabled == 'ENTRIES_DISABLED' and disabled_empty,
                      expired_completion_blocked=expired == 'COMPLETION_EXPIRED' and expired_empty,
                      revoked_entry_blocked=revoked == 'ENTRIES_DISABLED',
                      synthetic_entry_reserved=opened == 'OPENED' and reserved,
                      duplicate_signal_blocked=duplicate == 'DUPLICATE_SIGNAL' and one_position,
                      durable_restart_reconciled=recovered,
                      protection_with_worker_closed_and_entries_disabled=settled,
                      repeated_protection_idempotent=idempotent)
        return dict(exit_case=exit_kind, passed=all(checks.values()), checks=checks,
                    simulated_fills=events, positions_remaining=len(state['positions']))
    finally:
        if parent is not None:
            parent.close()
        else:
            worker.close()
        if ledger is not None:
            ledger.close()


def check():
    with TemporaryDirectory(prefix='atlas-disconnected-crypto-') as directory:
        cases = [_scenario(Path(directory)/name, name) for name in ('TARGET', 'STOP')]
    return dict(scope='OFFLINE_SYNTHETIC_CRYPTO_WORKER_LEDGER_V1', policy=POLICY,
                ready=all(case['passed'] for case in cases), cases=cases,
                broker_orders=0, network_requests=0, grok_requests=0,
                installed_databases_opened=0, baseline_modified=False,
                limitations=['Invented bars and quotes; not strategy performance or actual feed replay',
                             'Temporary local simulator fills only; no broker or desktop activation',
                             'Does not validate online Grok, continuous data capture or real execution'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        report = check()
    except Exception as error:
        # Preserve a small diagnostic without serializing local paths or payloads.
        report = dict(scope='OFFLINE_SYNTHETIC_CRYPTO_WORKER_LEDGER_V1', ready=False,
                      status='SYNTHETIC_CHECK_FAILED', error_type=type(error).__name__,
                      broker_orders=0, network_requests=0, grok_requests=0,
                      baseline_modified=False)
    rendered = json.dumps(report, indent=2)
    if args.output:
        # Do not silently replace another diagnostic or workstation file.
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(rendered+'\n')
    print(rendered)
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

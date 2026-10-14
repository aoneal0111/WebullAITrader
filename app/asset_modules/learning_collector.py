"""Periodic observational outcome collection; no broker, model or policy authority."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
from threading import Event, Lock, RLock, Thread, current_thread
from types import MappingProxyType

from app.asset_modules.grok_learning import LearningJournal
from app.asset_modules.learning_outcomes import read_equity_outcomes
from app.asset_modules.crypto_learning_outcomes import read_crypto_outcomes
from app.asset_modules.contract_learning_outcomes import read_contract_outcomes

LOG = logging.getLogger(__name__)
ENGINES = ("EQUITY", "CRYPTO", "OPTIONS", "FUTURES")


def source_paths(equity_path, execution_path=None):
    equity = Path(equity_path)
    base = Path(execution_path) if execution_path is not None else equity
    return dict(EQUITY=equity, CRYPTO=base.with_name('crypto-paper.sqlite3'),
                OPTIONS=base.with_name('options-paper.sqlite3'),
                FUTURES=base.with_name('futures-paper.sqlite3'))


class LearningOutcomeCollector:
    """One non-overlapping bounded scan per interval, explicitly started.

    No SQLite handle crosses threads. Stop interrupts the cadence wait; an
    in-flight read/import is not forcibly killed. A timed-out worker stays
    reserved and cannot be restarted. The next launch catches pending outcomes.
    """
    def __init__(self, journal_path, sources, *, interval_seconds=60):
        if set(sources) != set(ENGINES):
            raise ValueError("Four explicit outcome sources required")
        if type(interval_seconds) is not int or not 60 <= interval_seconds <= 3600:
            raise ValueError("Collection interval must be 60 to 3600 seconds")
        self.journal_path = Path(journal_path).resolve()
        self.sources = MappingProxyType({k:Path(sources[k]).resolve() for k in ENGINES})
        self.interval_seconds = interval_seconds
        self._validate_paths()
        self._stop, self._lock, self._tick_lock = Event(), RLock(), Lock()
        self._thread, self._closed, self._start_failed = None, False, False
        self._cycles, self._latest = 0, None

    def _validate_paths(self):
        paths = [self.journal_path, *self.sources.values()]
        if len({p.resolve() for p in paths}) != len(paths):
            raise ValueError("Learning journal and source paths must be distinct")
        for index, path in enumerate(paths):
            for other in paths[index+1:]:
                if path.exists() and other.exists() and path.samefile(other):
                    raise ValueError("Learning journal and sources must not alias")

    def snapshot(self):
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            active = running or self._tick_lock.locked()
            state = ("STOPPING" if active else "STOPPED") if self._closed else (
                "RUNNING" if active else "UNAVAILABLE" if self._start_failed else "READY")
            return {"scope":"OBSERVATIONAL_COLLECTION_NO_MODEL_CALLS_OR_ORDERS",
                    "state":state, "cycles":self._cycles, "last_cycle":deepcopy(self._latest),
                    "interval_seconds":self.interval_seconds, "network_requests":0,
                    "orders":0, "promotion":"NONE"}

    def _publish(self, report):
        with self._lock:
            self._cycles += 1
            self._latest = deepcopy(report)
        return report

    def collect_once(self):
        if not self._tick_lock.acquire(blocking=False):
            return {"state":"BUSY", "sources":{}, "orders":0, "network_requests":0}
        try:
            if self._stop.is_set():
                return {"state":"STOP_REQUESTED", "sources":{}, "orders":0, "network_requests":0}
            report = {"state":"COLLECTED", "sources":{},
                      "completed_at":None, "orders":0, "network_requests":0, "promotion":"NONE"}
            try:
                self._validate_paths()  # Recheck aliases before opening the target.
                journal = LearningJournal(self.journal_path)
            except Exception as exc:
                report.update(state="JOURNAL_UNAVAILABLE", error_class=type(exc).__name__)
                LOG.warning("Outcome collection journal unavailable (%s)", type(exc).__name__)
                return self._publish(report)
            readers = {"EQUITY":read_equity_outcomes, "CRYPTO":read_crypto_outcomes,
                       "OPTIONS":lambda p:read_contract_outcomes(p,"OPTIONS"),
                       "FUTURES":lambda p:read_contract_outcomes(p,"FUTURES")}
            for engine, path in self.sources.items():
                if self._stop.is_set():
                    report["state"] = "STOP_REQUESTED"
                    break
                stage = "READ"
                try:
                    if not path.is_file():
                        report["sources"][engine] = {"state":"MISSING_SOURCE"}
                        continue
                    source = readers[engine](path)
                    if self._stop.is_set():
                        report["state"] = "STOP_REQUESTED"
                        break
                    stage = "IMPORT"
                    saved = (journal.ingest_outcomes(source["outcomes"]) if engine == "EQUITY"
                             else journal.ingest_proxy_outcomes(source["outcomes"]))
                    report["sources"][engine] = {"state":"IMPORTED", **saved,
                        "closed_outcomes":len(source["outcomes"]), "excluded":source["excluded"]}
                except Exception as exc:
                    report["state"] = "PARTIAL_SOURCE_FAILURE"
                    report["sources"][engine] = {"state":stage+"_FAILED", "error_class":type(exc).__name__}
                    LOG.warning("Outcome collection %s %s failed (%s)", engine, stage, type(exc).__name__)
            report["completed_at"] = datetime.now(timezone.utc).isoformat()
            return self._publish(report)
        finally:
            self._tick_lock.release()

    def start(self):
        with self._lock:
            if self._closed:
                return False
            if self._thread is not None:
                return self._thread.is_alive()
            try:
                self._thread = Thread(target=self._run, name="atlas-learning-outcomes", daemon=True)
                self._thread.start()
            except Exception:
                self._thread, self._start_failed = None, True
                LOG.warning("Outcome collection worker unavailable")
                return False
            self._start_failed = False
            return True

    def _run(self):
        while not self._stop.is_set():
            self.collect_once()
            if self._stop.wait(self.interval_seconds):
                break

    def close(self, *, timeout_seconds=2.0):
        if (not isinstance(timeout_seconds,(int,float)) or not math.isfinite(timeout_seconds)
                or not 0 <= timeout_seconds <= 2):
            raise ValueError("Collection shutdown wait must be zero to two seconds")
        self._stop.set()
        with self._lock:
            self._closed, thread = True, self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout_seconds)
        return (thread is None or not thread.is_alive()) and not self._tick_lock.locked()


def main(argv=None):
    parser = argparse.ArgumentParser(description="One offline outcome collection cycle; sources read-only")
    parser.add_argument("--execution-ledger", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args(argv)
    collector = LearningOutcomeCollector(args.journal, source_paths(args.execution_ledger))
    try:
        report = collector.collect_once()
        print(json.dumps(report, indent=2))
        return int(report["state"] in {"PARTIAL_SOURCE_FAILURE", "JOURNAL_UNAVAILABLE"})
    finally:
        collector.close()


if __name__ == "__main__":
    raise SystemExit(main())

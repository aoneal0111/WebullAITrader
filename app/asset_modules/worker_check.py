"""Offline subprocess health check. Does not launch the Atlas GUI or trading."""
import json
import time

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.engine_worker import EngineWorker, WorkerState


def check_workers():
    workers = [EngineWorker(engine, policy_version="OBSERVATION_TRANSPORT_V1", timeout_seconds=10)
               for engine in EngineId]
    try:
        for worker in workers:
            worker.start()
        replies = {}
        while len(replies) < len(workers):
            for worker in workers:
                if worker.engine in replies:
                    continue
                reply = worker.poll()
                if worker.state == WorkerState.FAILED:
                    raise RuntimeError(str(worker.status()))
                if reply:
                    replies[worker.engine] = reply
            time.sleep(0.01)
        for worker in workers:
            worker.submit("PING")
        replies.clear()
        while len(replies) < len(workers):
            for worker in workers:
                if worker.engine in replies:
                    continue
                reply = worker.poll()
                if worker.state == WorkerState.FAILED:
                    raise RuntimeError(str(worker.status()))
                if reply:
                    replies[worker.engine] = reply
            time.sleep(0.01)
        if len({worker.pid for worker in workers}) != len(workers):
            raise RuntimeError("Workers did not receive separate process identities")
        return [worker.status() for worker in workers]
    finally:
        for worker in workers:
            worker.close()


if __name__ == "__main__":
    print(json.dumps({"scope": "OFFLINE_OBSERVATION_WORKERS_NO_TRADING", "workers": check_workers()}, indent=2))

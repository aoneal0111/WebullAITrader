from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Lock
import time

from app.catalysts.discovery import (
    CatalystDiscoveryRuntime,
    CatalystDiscoveryService,
)


NOW = datetime(2026, 9, 19, 8, 50, tzinfo=UTC)


@dataclass(frozen=True)
class Story:
    title: str
    published_at: datetime
    source_url: str
    provider_event_id: str


class Source:
    name = "OFFICIAL_NEWS"

    def __init__(self, stories):
        self._stories = tuple(stories)

    def recent_stories(self, as_of=None):
        return self._stories


def _service(tmp_path, source):
    return CatalystDiscoveryService(
        (source,),
        lambda: ("ABCD", "EFGH"),
        path=tmp_path / "watch-seeds.json",
        clock=lambda: NOW,
    )


def test_startup_backfill_creates_only_strict_authoritative_ticker_seeds(tmp_path):
    service = CatalystDiscoveryService(
        (
            Source(
                (
                    Story(
                        "ABCD wins major FDA clearance",
                        NOW - timedelta(hours=2),
                        "https://example.test/one",
                        "one",
                    ),
                    Story(
                        "abcd reports a contract",
                        NOW - timedelta(hours=1),
                        "https://example.test/two",
                        "two",
                    ),
                    Story(
                        "FAKE announces results",
                        NOW - timedelta(minutes=30),
                        "https://example.test/three",
                        "three",
                    ),
                )
            ),
        ),
        lambda: ("ABCD", "XYZ"),
        path=tmp_path / "watch-seeds.json",
        clock=lambda: NOW,
    )

    seeds = service.refresh()

    assert tuple(seed.symbol for seed in seeds) == ("ABCD",)
    assert service.symbols() == ("ABCD",)


def test_discovery_is_bounded_deduplicated_and_restart_safe(tmp_path):
    stories = tuple(
        Story(
            f"SYM{i} announces a material update",
            NOW - timedelta(minutes=i),
            f"https://example.test/{i}",
            str(i),
        )
        for i in range(5)
    )
    path = tmp_path / "watch-seeds.json"
    source = Source(stories)
    service = CatalystDiscoveryService(
        (source,),
        lambda: tuple(f"SYM{i}" for i in range(5)),
        path=path,
        maximum_seeds=3,
        clock=lambda: NOW,
    )

    first = service.refresh()
    second = service.refresh()
    restored = CatalystDiscoveryService(
        (source,),
        lambda: tuple(f"SYM{i}" for i in range(5)),
        path=path,
        maximum_seeds=3,
        clock=lambda: NOW,
    )

    assert [seed.symbol for seed in first] == ["SYM0", "SYM1", "SYM2"]
    assert second == first
    assert restored.snapshot() == first


def test_stale_and_ambiguous_headline_tokens_do_not_seed():
    service = CatalystDiscoveryService(
        (
            Source(
                (
                    Story(
                        "AI reports results",
                        NOW - timedelta(minutes=5),
                        "https://example.test/ambiguous",
                        "ambiguous",
                    ),
                    Story(
                        "XYZ reports results",
                        NOW - timedelta(hours=25),
                        "https://example.test/stale",
                        "stale",
                    ),
                )
            ),
        ),
        lambda: ("AI", "XYZ"),
        clock=lambda: NOW,
    )

    assert service.refresh() == ()


def test_runtime_refreshes_once_per_interval_and_preserves_watch_set(tmp_path):
    source = Source(
        (
            Story(
                "ABCD announces results",
                NOW - timedelta(minutes=5),
                "https://example.test/one",
                "one",
            ),
        )
    )
    service = CatalystDiscoveryService(
        (source,),
        lambda: ("ABCD",),
        path=tmp_path / "watch-seeds.json",
        clock=lambda: NOW,
    )
    ticks = iter((10.0, 11.0, 400.0))
    runtime = CatalystDiscoveryRuntime(
        service,
        refresh_seconds=300,
        monotonic=lambda: next(ticks),
    )

    assert runtime.symbols() == ("ABCD",)
    assert runtime.symbols() == ("ABCD",)
    assert runtime.symbols() == ("ABCD",)


def test_nonblocking_runtime_returns_cached_snapshot_and_refreshes_async(tmp_path):
    started = Event()
    release = Event()

    class BlockingSource(Source):
        def recent_stories(self, as_of=None):
            started.set()
            release.wait(2.0)
            return self._stories

    source = BlockingSource((Story(
        "ABCD announces results", NOW - timedelta(minutes=5),
        "https://example.test/one", "one",
    ),))
    runtime = CatalystDiscoveryRuntime(
        _service(tmp_path, source), monotonic=lambda: 10.0,
    )
    runtime._symbols = ("CACHED",)

    assert runtime.symbols_nonblocking() == ("CACHED",)
    assert started.wait(1.0)
    assert runtime.symbols_nonblocking() == ("CACHED",)
    release.set()
    for _ in range(40):
        if runtime.symbols_nonblocking() == ("ABCD",):
            break
        time.sleep(0.01)
    assert runtime.symbols_nonblocking() == ("ABCD",)


def test_nonblocking_runtime_creates_one_worker_and_publishes_once(tmp_path):
    started = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    class BlockingSource(Source):
        def recent_stories(self, as_of=None):
            nonlocal calls
            with calls_lock:
                calls += 1
            started.set()
            release.wait(2.0)
            return self._stories

    source = BlockingSource((Story(
        "ABCD announces results", NOW - timedelta(minutes=5),
        "https://example.test/one", "one",
    ),))
    runtime = CatalystDiscoveryRuntime(_service(tmp_path, source), monotonic=lambda: 10.0)
    published = []
    runtime.set_refresh_callback(published.append)

    for _ in range(10):
        assert runtime.symbols_nonblocking() == ()
    assert started.wait(1.0)
    release.set()
    for _ in range(40):
        if published:
            break
        time.sleep(0.01)
    assert calls == 1
    assert published == [("ABCD",)]


def test_nonblocking_runtime_failure_preserves_snapshot_and_retries_on_due_gate(tmp_path):
    class FailingService(CatalystDiscoveryService):
        def symbols_after_refresh(self, as_of=None):
            raise RuntimeError("provider unavailable")

    runtime = CatalystDiscoveryRuntime(
        FailingService((Source(()),), lambda: (), path=tmp_path / "x.json"),
        monotonic=lambda: 10.0,
    )
    runtime._symbols = ("CACHED",)
    assert runtime.symbols_nonblocking() == ("CACHED",)
    for _ in range(40):
        if runtime._refresh_thread is not None and not runtime._refresh_thread.is_alive():
            break
        time.sleep(0.01)
    assert runtime.symbols_nonblocking() == ("CACHED",)


def test_nonblocking_runtime_stale_worker_cannot_publish_after_stop(tmp_path):
    started = Event()
    release = Event()

    class BlockingSource(Source):
        def recent_stories(self, as_of=None):
            started.set()
            release.wait(2.0)
            return self._stories

    source = BlockingSource((Story(
        "ABCD announces results", NOW - timedelta(minutes=5),
        "https://example.test/one", "one",
    ),))
    runtime = CatalystDiscoveryRuntime(_service(tmp_path, source), monotonic=lambda: 10.0)
    published = []
    runtime.set_refresh_callback(published.append)
    assert runtime.symbols_nonblocking() == ()
    assert started.wait(1.0)
    runtime.stop()
    release.set()
    time.sleep(0.05)
    assert published == []
    assert runtime.symbols_nonblocking() == ()

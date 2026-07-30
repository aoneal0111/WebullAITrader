from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from threading import Event, RLock, Thread, current_thread
from time import perf_counter

from app.operations.runtime import PaperRuntimeEvent
from app.operations_core import ApplicationState
from app.runtime_event_replay import (
    ReplayControl,
    ReplayEngine,
    ReplayProgress,
    ReplayResult,
    ReplaySpeedControl,
    ReplayStatus,
)

from .models import ReplayWorkspacePhase, ReplayWorkspaceState


ReplayStateListener = Callable[[ApplicationState], None]
ReplayPacerFactory = Callable[
    [float, ReplayControl, Event],
    ReplaySpeedControl,
]


class TimestampReplaySpeed:
    """Pace recorded timestamps at an operator-selected multiplier."""

    def __init__(
        self,
        speed: float,
        control: ReplayControl,
        wake_event: Event,
    ) -> None:
        self._speed = speed
        self._control = control
        self._wake_event = wake_event

    def pace(
        self,
        previous_event: PaperRuntimeEvent | None,
        next_event: PaperRuntimeEvent,
    ) -> None:
        if previous_event is None or self._control.cancelled:
            return
        delay = max(
            0.0,
            (
                next_event.timestamp - previous_event.timestamp
            ).total_seconds()
            / self._speed,
        )
        self._wake_event.wait(delay)
        self._wake_event.clear()


class ReplayWorkspace:
    """Operator controls and immutable presentation state around ReplayEngine."""

    def __init__(
        self,
        engine: ReplayEngine,
        *,
        replay_speed: float = 1.0,
        pacer_factory: ReplayPacerFactory = TimestampReplaySpeed,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        if not isinstance(engine, ReplayEngine):
            raise TypeError("engine must be a ReplayEngine")
        if not callable(pacer_factory):
            raise TypeError("pacer_factory must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._engine = engine
        self._pacer_factory = pacer_factory
        self._clock = clock
        self._lock = RLock()
        self._control = ReplayControl()
        self._wake_event = Event()
        self._worker: Thread | None = None
        self._cancel_requested = False
        self._listeners: dict[int, ReplayStateListener] = {}
        self._next_listener_id = 1
        self._elapsed_before_play = 0.0
        self._play_started_at: float | None = None
        self._replay_state = ReplayWorkspaceState(
            total_events=len(engine.ordered_events),
            replay_speed=replay_speed,
        )

    @property
    def replay_state(self) -> ReplayWorkspaceState:
        with self._lock:
            return self._replay_state

    @property
    def state(self) -> ApplicationState:
        with self._lock:
            return self._application_state()

    def subscribe(self, listener: ReplayStateListener) -> int:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            listener_id = self._next_listener_id
            self._next_listener_id += 1
            self._listeners[listener_id] = listener
            state = self._application_state()
        listener(state)
        return listener_id

    def unsubscribe(self, listener_id: int) -> bool:
        with self._lock:
            return self._listeners.pop(listener_id, None) is not None

    def play(self) -> bool:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return False
            if self._engine.next_index >= len(self._engine.ordered_events):
                self._set_phase(ReplayWorkspacePhase.COMPLETED)
                notification = self._notification()
                worker = None
            else:
                self._control.reset()
                self._wake_event.clear()
                self._cancel_requested = False
                self._elapsed_before_play = (
                    self._replay_state.elapsed_seconds
                )
                self._play_started_at = self._clock()
                self._set_phase(ReplayWorkspacePhase.PLAYING)
                worker = Thread(
                    target=self._run_playback,
                    name="atlas-replay-workspace",
                    daemon=True,
                )
                self._worker = worker
                notification = self._notification()
        self._notify(notification)
        if worker is not None:
            worker.start()
            return True
        return False

    def pause(self) -> bool:
        with self._lock:
            if not self._replay_state.active:
                return False
            self._control.cancel()
            self._wake_event.set()
            self._set_phase(ReplayWorkspacePhase.PAUSED)
            notification = self._notification()
        self._notify(notification)
        return True

    def cancel(self) -> bool:
        with self._lock:
            if not self._replay_state.active:
                return False
            self._cancel_requested = True
            self._control.cancel()
            self._wake_event.set()
            self._set_phase(ReplayWorkspacePhase.CANCELLED)
            notification = self._notification()
        self._notify(notification)
        return True

    def step(self) -> bool:
        if not self._prepare_synchronous_command():
            return False
        result = self._engine.step()
        self._apply_result(result, reset_elapsed=False)
        return result.processed_events == 1

    def restart(self) -> None:
        self._stop_worker()
        self._engine.reset()
        with self._lock:
            self._replay_state = ReplayWorkspaceState(
                total_events=len(self._engine.ordered_events),
                replay_speed=self._replay_state.replay_speed,
            )
            notification = self._notification()
        self._notify(notification)

    def jump_to_timestamp(self, timestamp: datetime) -> None:
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        self._stop_worker()
        result = self._engine.replay(to_timestamp=timestamp)
        self._apply_result(result, reset_elapsed=True)

    def jump_to_event_index(self, event_index: int) -> None:
        if (
            isinstance(event_index, bool)
            or not isinstance(event_index, int)
            or not 0 <= event_index <= len(self._engine.ordered_events)
        ):
            raise ValueError("event_index is outside the event stream")
        self._stop_worker()
        self._engine.reset()
        elapsed = 0.0
        for _ in range(event_index):
            result = self._engine.step()
            elapsed += result.statistics.elapsed_seconds
        with self._lock:
            phase = (
                ReplayWorkspacePhase.COMPLETED
                if event_index == len(self._engine.ordered_events)
                and event_index > 0
                else ReplayWorkspacePhase.PAUSED
            )
            self._replay_state = ReplayWorkspaceState(
                phase=phase,
                current_event=event_index,
                events_processed=event_index,
                total_events=len(self._engine.ordered_events),
                replay_speed=self._replay_state.replay_speed,
                elapsed_seconds=elapsed,
            )
            notification = self._notification()
        self._notify(notification)

    def wait(self, timeout: float | None = None) -> bool:
        with self._lock:
            worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def close(self) -> None:
        self._stop_worker()
        with self._lock:
            self._listeners.clear()
        self._engine.close()

    def _run_playback(self) -> None:
        pacer = self._pacer_factory(
            self.replay_state.replay_speed,
            self._control,
            self._wake_event,
        )
        result = self._engine.resume(
            speed=pacer,
            control=self._control,
            progress=self._on_progress,
        )
        with self._lock:
            elapsed = self._elapsed_now()
            if result.status is ReplayStatus.COMPLETED:
                phase = ReplayWorkspacePhase.COMPLETED
            elif self._cancel_requested:
                phase = ReplayWorkspacePhase.CANCELLED
            else:
                phase = ReplayWorkspacePhase.PAUSED
            self._replay_state = replace(
                self._replay_state,
                phase=phase,
                current_event=result.next_index,
                events_processed=result.next_index,
                elapsed_seconds=elapsed,
            )
            self._play_started_at = None
            self._worker = None
            notification = self._notification()
        self._notify(notification)

    def _on_progress(self, progress: ReplayProgress) -> None:
        with self._lock:
            self._replay_state = replace(
                self._replay_state,
                current_event=progress.current_event,
                events_processed=progress.current_event,
                elapsed_seconds=self._elapsed_now(),
            )
            notification = self._notification()
        self._notify(notification)

    def _apply_result(
        self,
        result: ReplayResult,
        *,
        reset_elapsed: bool,
    ) -> None:
        with self._lock:
            prior_elapsed = (
                0.0 if reset_elapsed else self._replay_state.elapsed_seconds
            )
            phase = (
                ReplayWorkspacePhase.COMPLETED
                if result.status in {ReplayStatus.COMPLETED, ReplayStatus.EMPTY}
                else ReplayWorkspacePhase.PAUSED
            )
            self._replay_state = ReplayWorkspaceState(
                phase=phase,
                current_event=result.next_index,
                events_processed=result.next_index,
                total_events=result.total_events,
                replay_speed=self._replay_state.replay_speed,
                elapsed_seconds=(
                    prior_elapsed + result.statistics.elapsed_seconds
                ),
            )
            notification = self._notification()
        self._notify(notification)

    def _prepare_synchronous_command(self) -> bool:
        with self._lock:
            return not (
                self._worker is not None and self._worker.is_alive()
            )

    def _stop_worker(self) -> None:
        with self._lock:
            worker = self._worker
            if worker is not None and worker.is_alive():
                self._control.cancel()
                self._wake_event.set()
        if worker is not None and worker is not current_thread():
            worker.join()

    def _elapsed_now(self) -> float:
        if self._play_started_at is None:
            return self._replay_state.elapsed_seconds
        return self._elapsed_before_play + max(
            0.0,
            self._clock() - self._play_started_at,
        )

    def _set_phase(self, phase: ReplayWorkspacePhase) -> None:
        self._replay_state = replace(self._replay_state, phase=phase)

    def _application_state(self) -> ApplicationState:
        return replace(self._engine.state, replay=self._replay_state)

    def _notification(
        self,
    ) -> tuple[ApplicationState, tuple[ReplayStateListener, ...]]:
        return self._application_state(), tuple(self._listeners.values())

    @staticmethod
    def _notify(
        notification: tuple[
            ApplicationState,
            tuple[ReplayStateListener, ...],
        ],
    ) -> None:
        state, listeners = notification
        for listener in listeners:
            listener(state)


__all__ = [
    "ReplayPacerFactory",
    "ReplayStateListener",
    "ReplayWorkspace",
    "TimestampReplaySpeed",
]

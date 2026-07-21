from __future__ import annotations

import time

from PySide6.QtCore import QThread

from app.operations_core import (
    OperationsBus,
    RuntimeCycleCompleted,
    RuntimeFailed,
    RuntimeStarted,
    RuntimeStarting,
    RuntimeStopped,
    RuntimeStopping,
)


class RuntimeWorker(QThread):
    """
    Temporary paper-runtime simulator connected to the Operations Bus.

    It performs no broker mutations and submits no orders. A later milestone
    will replace its simulated loop with an adapter around the existing paper
    operations engine.
    """

    def __init__(
        self,
        bus: OperationsBus,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self._bus = bus
        self._cycle_count = 0
        self._stopping_event_published = False

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    def request_stop(
        self,
        reason: str = "Operator requested shutdown",
    ) -> None:
        if not self._stopping_event_published:
            self._stopping_event_published = True
            self._bus.publish(
                RuntimeStopping(
                    source="desktop-runtime",
                    reason=reason,
                )
            )

        self.requestInterruption()

    def run(self) -> None:
        try:
            self._bus.publish(
                RuntimeStarting(
                    source="desktop-runtime",
                    environment="PAPER",
                )
            )

            if self._sleep_interruptibly(0.8):
                self._publish_stopped()
                return

            self._bus.publish(
                RuntimeStarted(
                    source="desktop-runtime",
                    environment="PAPER",
                    active_model="Promoted model",
                )
            )

            while not self.isInterruptionRequested():
                self._cycle_count += 1

                self._bus.publish(
                    RuntimeCycleCompleted(
                        source="desktop-runtime",
                        cycle_count=self._cycle_count,
                    )
                )

                if self._sleep_interruptibly(1.0):
                    break

            self._publish_stopped()

        except Exception as exc:
            self._bus.publish(
                RuntimeFailed(
                    source="desktop-runtime",
                    error_message=str(exc),
                )
            )

    def _publish_stopped(self) -> None:
        self._bus.publish(
            RuntimeStopped(
                source="desktop-runtime",
                reason="Paper runtime stopped cleanly.",
                cycles_completed=self._cycle_count,
            )
        )

    def _sleep_interruptibly(self, seconds: float) -> bool:
        remaining = max(0.0, seconds)

        while remaining > 0:
            if self.isInterruptionRequested():
                return True

            interval = min(0.05, remaining)
            time.sleep(interval)
            remaining -= interval

        return self.isInterruptionRequested()

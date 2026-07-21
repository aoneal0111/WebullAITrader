from __future__ import annotations

import time

from PySide6.QtCore import QThread, Signal

from app.gui.models import DashboardSnapshot, RuntimeState


class RuntimeWorker(QThread):
    """
    Temporary GUI runtime worker.

    This worker simulates paper-runtime activity. It performs no broker
    mutations and submits no orders. It will later be replaced by an adapter
    around the existing paper runtime.
    """

    snapshot_changed = Signal(object)
    runtime_failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cycle_count = 0

    def run(self) -> None:
        try:
            self.snapshot_changed.emit(
                DashboardSnapshot(
                    environment="PAPER",
                    runtime_state=RuntimeState.STARTING,
                    broker_status="Connecting",
                    market_feed_status="Starting",
                    inference_status="Loading",
                    emergency_stop_enabled=True,
                    active_model="Loading",
                    cycle_count=self._cycle_count,
                    status_message="Starting paper runtime...",
                )
            )

            if self._sleep_interruptibly(0.8):
                return

            self.snapshot_changed.emit(
                DashboardSnapshot(
                    environment="PAPER",
                    runtime_state=RuntimeState.RUNNING,
                    broker_status="Connected",
                    market_feed_status="Healthy",
                    inference_status="Healthy",
                    emergency_stop_enabled=True,
                    active_model="Promoted model",
                    cycle_count=self._cycle_count,
                    status_message="Paper runtime is running.",
                )
            )

            while not self.isInterruptionRequested():
                self._cycle_count += 1

                self.snapshot_changed.emit(
                    DashboardSnapshot(
                        environment="PAPER",
                        runtime_state=RuntimeState.RUNNING,
                        broker_status="Connected",
                        market_feed_status="Healthy",
                        inference_status="Healthy",
                        emergency_stop_enabled=True,
                        active_model="Promoted model",
                        cycle_count=self._cycle_count,
                        status_message=(
                            f"Paper runtime cycle {self._cycle_count} completed."
                        ),
                    )
                )

                if self._sleep_interruptibly(1.0):
                    break

            self.snapshot_changed.emit(
                DashboardSnapshot(
                    environment="PAPER",
                    runtime_state=RuntimeState.STOPPED,
                    broker_status="Disconnected",
                    market_feed_status="Idle",
                    inference_status="Ready",
                    emergency_stop_enabled=True,
                    active_model="Promoted model",
                    cycle_count=self._cycle_count,
                    status_message="Paper runtime stopped cleanly.",
                )
            )
        except Exception as exc:
            self.runtime_failed.emit(str(exc))

    def _sleep_interruptibly(self, seconds: float) -> bool:
        remaining = max(0.0, seconds)

        while remaining > 0:
            if self.isInterruptionRequested():
                return True

            interval = min(0.05, remaining)
            time.sleep(interval)
            remaining -= interval

        return self.isInterruptionRequested()
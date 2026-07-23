from __future__ import annotations

from collections.abc import Callable
from threading import Event
class SimulatedPaperRuntimeDriver:

    """

    Temporary non-trading driver used until PaperOperationsEngine is composed.



    It has no broker, order, position, scanner, or market-data capability.

    """



    def __init__(

        self,

        *,

        interval_seconds: float = 1.0,

        environment: str = "PAPER",

        active_model: str = "Promoted model",

    ) -> None:

        if interval_seconds < 0:

            raise ValueError("interval_seconds must be nonnegative")



        if not environment.strip():

            raise ValueError("environment must not be empty")



        if not active_model.strip():

            raise ValueError("active_model must not be empty")



        self._interval_seconds = interval_seconds

        self._environment = environment.strip()

        self._active_model = active_model.strip()

        self._cycles_completed = 0



    @property

    def environment(self) -> str:

        return self._environment



    @property

    def active_model(self) -> str:

        return self._active_model



    @property

    def cycles_completed(self) -> int:

        return self._cycles_completed



    def run(

        self,

        *,

        stop_event: Event,

        cycle_sink: Callable[[int], None],

    ) -> None:

        while not stop_event.is_set():

            self._cycles_completed += 1

            cycle_sink(self._cycles_completed)



            if stop_event.wait(self._interval_seconds):

                break

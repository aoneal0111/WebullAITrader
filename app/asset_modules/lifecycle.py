from dataclasses import dataclass
from threading import RLock
from typing import Callable

from app.assets import AssetType


@dataclass(frozen=True)
class ModuleAdapter:
    start: Callable[[], object]
    stop: Callable[[], bool]
    active: Callable[[], bool]
    has_exposure: Callable[[], bool]


class AssetModules:
    """Serialized lifecycle authority; stopping never abandons exposure."""

    def __init__(self, adapters: dict[AssetType, ModuleAdapter], maximum_active: int = 2):
        if not 1 <= maximum_active <= len(AssetType):
            raise ValueError("Invalid active module budget")
        self.adapters = dict(adapters)
        self.maximum_active = maximum_active
        self._lock = RLock()

    def set_budget(self, maximum_active):
        with self._lock:
            if maximum_active not in (1, 2):
                raise ValueError('Choose one or two active markets')
            if sum(item.active() for item in self.adapters.values()) > maximum_active:
                raise ValueError('Stop a market before reducing the active market budget')
            self.maximum_active = maximum_active

    def activate(self, asset: AssetType) -> None:
        with self._lock:
            adapter = self.adapters.get(asset)
            if adapter is None:
                raise ValueError(f"{asset.value.title()} adapter is not installed")
            if adapter.active():
                return
            if sum(item.active() for item in self.adapters.values()) >= self.maximum_active:
                raise ValueError("Active market limit reached; stop another market first")
            result = adapter.start()
            if result is False:
                raise RuntimeError("Market could not start; inspect its configuration")

    def deactivate(self, asset: AssetType) -> None:
        with self._lock:
            adapter = self.adapters[asset]
            if adapter.has_exposure():
                raise ValueError("Open positions or orders require management; market remains active")
            if not adapter.stop():
                raise RuntimeError("Shutdown still pending; workers remain reserved")

    def status(self, asset: AssetType) -> str:
        adapter = self.adapters.get(asset)
        if adapter is None:
            return "NOT AVAILABLE"
        return "ACTIVE" if adapter.active() else "OFF"

"""Presenter coordinating chart symbol selection and REST projection loads."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging

from PySide6.QtCore import QObject, Signal

from app.gui.models import ChartViewSnapshot
from app.gui.projections.chart_projection import ChartProjection
from app.operations_core import ApplicationState


_LOGGER = logging.getLogger("atlas.gui.chart")


class _ResultBridge(QObject):
    completed = Signal(int, object)


class ChartPresenter:
    """Request each selected symbol once and publish immutable chart models."""

    def __init__(
        self,
        view,
        projection: ChartProjection,
        *,
        default_symbol: str | None = None,
        asynchronous: bool = True,
    ) -> None:
        self._view = view
        self._projection = projection
        self._default_symbol = _symbol(default_symbol)
        self._selected_symbol: str | None = None
        self._timeframe = "1D"
        self._generation = 0
        self._closed = False
        self._executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="atlas-chart-rest")
            if asynchronous
            else None
        )
        self._bridge = _ResultBridge(view)
        self._bridge.completed.connect(self._apply)
        view.set_chart_managed(True)
        view.chart_symbol_selected.connect(self.select_symbol)
        view.chart_timeframe_selected.connect(self.select_timeframe)

    def render(self, state: ApplicationState) -> None:
        projected = _symbol(state.watchlist_projection.selected_symbol)
        symbol = projected or self._default_symbol
        source = "watchlist" if projected else "configured fallback"
        if symbol is None:
            _LOGGER.info(
                "operation=selected_symbol status=skipped symbol=-- "
                "reason=no watchlist selection or configured fallback"
            )
            _log_request_skips("--", "no watchlist selection or configured fallback")
            self._view.render_chart(ChartViewSnapshot())
            return
        _LOGGER.info(
            "operation=selected_symbol status=selected symbol=%s source=%s",
            symbol,
            source,
        )
        self._request(symbol)

    def select_symbol(self, symbol: str) -> None:
        normalized = _symbol(symbol)
        if normalized is None or normalized == "NO ACTIVE SYMBOL":
            _LOGGER.info(
                "operation=selected_symbol status=skipped symbol=-- "
                "reason=empty selector value"
            )
            return
        _LOGGER.info(
            "operation=selected_symbol status=selected symbol=%s source=chart selector",
            normalized,
        )
        self._request(normalized)

    def select_timeframe(self, timeframe: str) -> None:
        normalized = timeframe.strip().upper()
        if not normalized or normalized == self._timeframe:
            _LOGGER.info(
                "operation=historical_bar_request status=skipped symbol=%s "
                "reason=timeframe unchanged",
                self._selected_symbol or "--",
            )
            return
        self._timeframe = normalized
        if self._selected_symbol is None:
            _LOGGER.info(
                "operation=historical_bar_request status=skipped symbol=-- "
                "reason=no selected symbol"
            )
            return
        self._request(self._selected_symbol, force=True)

    def _request(self, symbol: str, *, force: bool = False) -> None:
        if self._closed:
            _log_request_skips(symbol, "chart presenter closed")
            return
        if symbol == self._selected_symbol and not force:
            _LOGGER.info(
                "operation=chart_model_update status=skipped symbol=%s "
                "reason=selection unchanged",
                symbol,
            )
            _log_request_skips(symbol, "selection unchanged")
            return
        self._selected_symbol = symbol
        self._generation += 1
        generation = self._generation
        self._view.render_chart(
            ChartViewSnapshot(
                symbol=symbol,
                timeframe=self._timeframe,
                message="Loading snapshot, quote, and historical candles through REST.",
            )
        )
        if self._executor is None:
            self._apply(generation, self._projection.request(symbol, self._timeframe))
            return
        future = self._executor.submit(
            self._projection.request, symbol, self._timeframe
        )
        future.add_done_callback(
            lambda completed: self._complete(generation, completed)
        )

    def _complete(self, generation: int, future: Future) -> None:
        try:
            model = future.result()
        except Exception as exc:
            model = ChartViewSnapshot(
                symbol=self._selected_symbol or "--",
                timeframe=self._timeframe,
                message=f"Chart REST projection failed ({type(exc).__name__}).",
            )
            _LOGGER.warning(
                "operation=chart_model_update status=failed symbol=%s error_type=%s",
                self._selected_symbol or "--",
                type(exc).__name__,
            )
        if not self._closed:
            try:
                self._bridge.completed.emit(generation, model)
            except RuntimeError:
                pass

    def _apply(self, generation: int, model: ChartViewSnapshot) -> None:
        if self._closed or generation != self._generation:
            _LOGGER.info(
                "operation=chart_model_update status=skipped symbol=%s "
                "reason=stale request generation",
                model.symbol,
            )
            return
        self._view.render_chart(model)

    def close(self) -> None:
        self._closed = True
        self._generation += 1
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)


def _symbol(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized and normalized != "--" else None


def _log_request_skips(symbol: str, reason: str) -> None:
    for operation in (
        "snapshot_request",
        "quote_request",
        "historical_bar_request",
    ):
        _LOGGER.info(
            "operation=%s status=skipped symbol=%s reason=%s",
            operation,
            symbol,
            reason,
        )


__all__ = ["ChartPresenter"]

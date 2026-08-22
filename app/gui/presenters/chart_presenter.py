"""Presenter coordinating chart symbol selection and REST projection loads."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal, InvalidOperation
import logging

from PySide6.QtCore import QObject, Signal

from app.gui.chart_inspection import ChartInspectionStore
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
        inspection_store: ChartInspectionStore | None = None,
    ) -> None:
        self._view = view
        self._projection = projection
        # Kept in the constructor for composition compatibility.  A configured
        # bootstrap symbol is deliberately not a presentation selection.
        del default_symbol
        self._selected_symbol: str | None = None
        self._inspection = inspection_store or ChartInspectionStore()
        self._atlas_symbol: str | None = None
        self._selection_source = "none"
        self._state = ApplicationState()
        self._last_model = ChartViewSnapshot()
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
        operator_signal = getattr(view, "operator_symbol_selected", None)
        (
            operator_signal
            if operator_signal is not None
            else view.chart_symbol_selected
        ).connect(self.select_symbol)
        if hasattr(view, "atlas_symbol_selected"):
            view.atlas_symbol_selected.connect(self.select_atlas_symbol)
        view.chart_timeframe_selected.connect(self.select_timeframe)

    def render(self, state: ApplicationState) -> None:
        self._state = state
        projected = _atlas_candidate_symbol(
            state,
            _symbol(state.watchlist_projection.selected_symbol),
        )
        atlas_focus = projected or (
            self._atlas_symbol
            if _atlas_candidate_symbol(state, self._atlas_symbol) is not None
            else None
        )
        position = _active_position_symbol(state)
        order = _working_order_symbol(state)
        operator_symbol = self._inspection.snapshot().symbol
        symbol = operator_symbol or atlas_focus or position or order
        source = next(
            name for value, name in (
                (operator_symbol, "operator inspection"),
                (atlas_focus, "atlas candidate"),
                (position, "active position"),
                (order, "working order"),
                (None, "none"),
            )
            if value == symbol
        )
        if symbol is None:
            _LOGGER.info(
                "operation=selected_symbol status=skipped symbol=-- "
                "reason=no operator, Atlas, position, or working-order selection"
            )
            _log_request_skips("--", "no active instrument source")
            self._generation += 1
            self._selected_symbol = None
            self._selection_source = "none"
            message = (
                "Atlas is scanning. No candidate is currently in focus."
                if (state.health_projection.scanner_status or "").upper() == "RUNNING"
                else "No active instrument. The market chart is idle."
            )
            self._last_model = ChartViewSnapshot(message=message)
            self._view.render_chart(self._decorate(self._last_model))
            return
        _LOGGER.info(
            "operation=selected_symbol status=selected symbol=%s source=%s",
            symbol,
            source,
        )
        self._selection_source = source
        if symbol == self._selected_symbol:
            self._view.render_chart(self._decorate(self._last_model))
            return
        self._request(symbol)

    def select_symbol(self, symbol: str) -> None:
        normalized = _symbol(symbol)
        if normalized is None or normalized == "NO ACTIVE SYMBOL":
            _LOGGER.info(
                "operation=selected_symbol status=skipped symbol=-- "
                "reason=empty selector value"
            )
            self.clear_inspection()
            return
        self._inspection.select(normalized)
        self._atlas_symbol = None
        self._selection_source = "operator inspection"
        _LOGGER.info(
            "operation=selected_symbol status=selected symbol=%s "
            "source=operator inspection",
            normalized,
        )
        self._request(normalized)

    def clear_inspection(self) -> None:
        """Clear only operator intent, then resume automatic priority."""

        prior = self._inspection.snapshot().symbol
        self._inspection.clear()
        _LOGGER.info(
            "operation=inspection_selection status=cleared symbol=%s",
            prior or "--",
        )
        self.render(self._state)

    def select_atlas_symbol(self, symbol: str) -> None:
        normalized = _symbol(symbol)
        if normalized is None:
            return
        self._atlas_symbol = normalized
        # Scanner/Atlas focus updates are lower priority and can never clear or
        # overwrite explicit operator inspection intent.
        self.render(self._state)

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
            self._last_model = replace(
                self._last_model,
                selection_source=self._selection_source,
            )
            self._view.render_chart(self._decorate(self._last_model))
            return
        self._selected_symbol = symbol
        self._generation += 1
        generation = self._generation
        self._last_model = ChartViewSnapshot(
                symbol=symbol,
                timeframe=self._timeframe,
                message="Loading snapshot, quote, and historical candles through REST.",
                selection_source=self._selection_source,
        )
        self._view.render_chart(self._decorate(self._last_model))
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
        self._last_model = replace(model, selection_source=self._selection_source)
        self._view.render_chart(self._decorate(self._last_model))

    def _decorate(self, model: ChartViewSnapshot) -> ChartViewSnapshot:
        health = self._state.health_projection
        operator_symbol = self._inspection.snapshot().symbol
        subscription_symbols = health.subscription_symbols
        inspection_has_live_projection = (
            operator_symbol is None
            or subscription_symbols is None
            or operator_symbol in subscription_symbols
        )
        return replace(
            model,
            selection_source=self._selection_source,
            last_stream_update=(
                health.last_market_data_event
                if inspection_has_live_projection
                else None
            ),
            stream_stale_after_seconds=health.market_data_stale_after_seconds,
            historical_data_available=(
                bool(model.candles)
                or health.historical_bars_status == "AVAILABLE"
                or health.market_data_rest_status in {"AVAILABLE", "CONNECTED"}
            ),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._inspection.clear()
        try:
            self._bridge.completed.disconnect(self._apply)
        except RuntimeError:
            pass
        if self._executor is not None:
            # Do not let a callback retain or signal into Qt objects after the
            # window's native children begin destruction.
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None


def _symbol(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized and normalized != "--" else None


def _active_position_symbol(state: ApplicationState) -> str | None:
    for position in state.positions:
        try:
            if Decimal(position.quantity) != 0:
                return _symbol(position.symbol)
        except (InvalidOperation, ValueError):
            continue
    return None


def _atlas_candidate_symbol(
    state: ApplicationState,
    symbol: str | None,
) -> str | None:
    if symbol is None:
        return None
    entry = next(
        (
            item
            for item in state.watchlist_projection.entries
            if item.symbol == symbol
        ),
        None,
    )
    return (
        symbol
        if entry is not None and dict(entry.metadata).get("scanner_rank") is not None
        else None
    )


_WORKING_ORDER_STATUSES = frozenset({
    "ACCEPTED", "NEW", "OPEN", "PARTIALLY_FILLED", "PENDING",
    "PENDING_CANCEL", "SUBMITTED", "WORKING",
})


def _working_order_symbol(state: ApplicationState) -> str | None:
    return next(
        (
            _symbol(order.symbol)
            for order in state.orders
            if order.status.strip().upper() in _WORKING_ORDER_STATUSES
        ),
        None,
    )


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

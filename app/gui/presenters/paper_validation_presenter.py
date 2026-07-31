from __future__ import annotations

from app.gui.models.paper_validation import PaperValidationDashboardSnapshot
from app.live_execution.paper_validation import PaperValidationReport


class PaperValidationPresenter:
    """Callable validator status sink that updates the dashboard panel."""

    def __init__(self, view) -> None:
        if not callable(getattr(view, "render", None)):
            raise TypeError("paper validation view must provide render(snapshot)")
        self._view = view
        self.snapshot = PaperValidationDashboardSnapshot.initial()

    def __call__(self, report: PaperValidationReport) -> None:
        self.render(report)

    def render(self, report: PaperValidationReport) -> None:
        self.snapshot = PaperValidationDashboardSnapshot.from_report(report)
        self._view.render(self.snapshot)


__all__ = ["PaperValidationPresenter"]

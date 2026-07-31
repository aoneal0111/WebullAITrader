from __future__ import annotations

from dataclasses import dataclass

from app.live_execution.paper_validation import PaperValidationReport


@dataclass(frozen=True, slots=True)
class PaperValidationDashboardSnapshot:
    account: str = "NOT RUN"
    orders: str = "NOT RUN"
    buying_power: str = "NOT RUN"
    positions: str = "NOT RUN"
    reconciliation: str = "NOT RUN"
    overall: str = "NOT RUN"
    message: str = "Not started"

    @classmethod
    def initial(cls) -> "PaperValidationDashboardSnapshot":
        return cls()

    @classmethod
    def from_report(cls, report: PaperValidationReport) -> "PaperValidationDashboardSnapshot":
        if not isinstance(report, PaperValidationReport):
            raise TypeError("report must be a PaperValidationReport")
        return cls(
            account=report.account.status.value,
            orders=report.orders.status.value,
            buying_power=report.buying_power.status.value,
            positions=report.positions.status.value,
            reconciliation=report.reconciliation.status.value,
            overall=report.overall.value,
            message=report.message,
        )


__all__ = ["PaperValidationDashboardSnapshot"]

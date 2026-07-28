from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QWidget


class WorkstationSplitter(QSplitter):
    """Responsive chart/workspace split with a 70/30 preferred ratio."""

    CHART_STRETCH = 7
    WORKSPACE_STRETCH = 3

    def __init__(
        self,
        chart: QWidget,
        workspace: QWidget,
    ) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setObjectName("workstationSplitter")
        self.setChildrenCollapsible(False)
        self.addWidget(chart)
        self.addWidget(workspace)
        self.setStretchFactor(0, self.CHART_STRETCH)
        self.setStretchFactor(1, self.WORKSPACE_STRETCH)
        self.setSizes([700, 300])


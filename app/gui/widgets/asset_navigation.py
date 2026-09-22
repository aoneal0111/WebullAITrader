"""Market navigation is independent of market lifecycle and execution."""
from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtWidgets import QWidget, QHBoxLayout, QTabBar, QPushButton, QLabel, QComboBox
from app.assets import AssetType


class _LifecycleTask(QThread):
    completed = Signal(str)

    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            self.action()
        except Exception as error:
            self.completed.emit(str(error))
        else:
            self.completed.emit("")


class AssetNavigation(QWidget):
    asset_selected = Signal(object)

    def __init__(self, modules=None, parent=None):
        super().__init__(parent)
        self.modules = modules
        self.assets = tuple(AssetType)
        self.task = None
        self.last_error = ""
        row = QHBoxLayout(self)
        self.tabs = QTabBar()
        for asset in self.assets:
            self.tabs.addTab(asset.value.title())
        row.addWidget(self.tabs)
        self.status = QLabel()
        row.addWidget(self.status, 1)
        self.budget = QComboBox()
        self.budget.addItems(['One active market', 'Up to two active markets'])
        self.budget.setCurrentIndex((modules.maximum_active if modules else 2)-1)
        self.budget.setEnabled(modules is not None)
        self.budget.currentIndexChanged.connect(self._budget_changed)
        row.addWidget(self.budget)
        self.toggle = QPushButton("Start market")
        row.addWidget(self.toggle)
        self.tabs.currentChanged.connect(self._select)
        self.toggle.clicked.connect(self._toggle)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _select(self, index):
        self.last_error = ""
        self.asset_selected.emit(self.assets[index])
        self.refresh()

    def refresh(self):
        asset = self.assets[self.tabs.currentIndex()]
        state = "NOT AVAILABLE" if self.modules is None else self.modules.status(asset)
        self.status.setText(self.last_error or f"{asset.value.title()}: {state}")
        self.toggle.setText("Stop market" if state == "ACTIVE" else "Start market")
        self.toggle.setEnabled(state != "NOT AVAILABLE" and self.task is None)

    def _budget_changed(self, index):
        try:
            self.modules.set_budget(index+1)
            self.last_error = ''
        except ValueError as error:
            self.last_error = str(error)
            self.budget.blockSignals(True)
            self.budget.setCurrentIndex(self.modules.maximum_active-1)
            self.budget.blockSignals(False)
        self.refresh()

    def run_action(self, asset, start):
        if self.task is not None:
            return
        action = self.modules.activate if start else self.modules.deactivate
        self.task = _LifecycleTask(lambda: action(asset), self)
        self.task.completed.connect(self._result)
        self.task.finished.connect(self._finished)
        self.toggle.setEnabled(False)
        self.task.start()

    def _toggle(self):
        asset = self.assets[self.tabs.currentIndex()]
        self.run_action(asset, self.modules.status(asset) != 'ACTIVE')

    def _result(self, message):
        self.last_error = message
        self.status.setToolTip(message)
        if message:
            self.status.setText(message)

    def _finished(self):
        self.task.deleteLater()
        self.task = None
        self.refresh()

    def shutdown(self):
        # Lifecycle calls have bounded shutdown waits. Never destroy a live QThread.
        if self.task is not None:
            self.task.wait()

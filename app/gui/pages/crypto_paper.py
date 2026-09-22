"""Separate crypto simulation views; equity rows are never reused."""
from decimal import Decimal
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton,
)


class CryptoPaperPage(QWidget):
    def __init__(self, title, supervisor):
        super().__init__()
        self.supervisor = supervisor
        self.title = title
        layout = QVBoxLayout(self)
        heading = QLabel(f'CRYPTO · {title.upper()}')
        heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        layout.addWidget(QLabel('PAPER · Spot USD pairs · Separate $10,000 starting balance'))
        disclosure = QLabel('Snapshot fills assume 0.1% fees and 0.1% slippage per side. Exchange queues and partial fills are not simulated.')
        disclosure.setWordWrap(True)
        layout.addWidget(disclosure)
        self.enabled = QCheckBox('Enable AI paper proposals — shares crypto quotes and simulated account data with Gemini')
        self.enabled.setChecked(supervisor.entries_enabled)
        self.enabled.setEnabled(supervisor.provider is not None)
        self.enabled.toggled.connect(self.set_enabled)
        layout.addWidget(self.enabled)
        self.state = QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.content = QTableWidget()
        self.content.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.content.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.content.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.content.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.content, 1)
        self.close_positions = QPushButton('Close crypto paper positions and pause AI proposals')
        self.close_positions.clicked.connect(self.close_paper_positions)
        layout.addWidget(self.close_positions)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def set_enabled(self, enabled):
        self.supervisor.entries_enabled = enabled

    def close_paper_positions(self):
        from uuid import uuid4
        supervisor = self.supervisor
        supervisor.entries_enabled = False
        quotes = {row.pair.canonical_symbol: row for row in supervisor.source()}
        errors = []
        for position in supervisor.paper.snapshot()['positions']:
            try:
                supervisor.paper.apply(dict(id=str(uuid4()), symbol=position['symbol'],
                                            action='SELL', reason='Operator paper close'), quotes)
            except (ValueError, KeyError, ArithmeticError):
                errors.append(position['symbol'])
        supervisor.status = ('Fresh quotes required to close: ' + ', '.join(errors)) if errors else 'Paper positions closed; AI proposals paused'
        self.refresh()

    def refresh(self):
        if not self.isVisible():
            return
        self.enabled.blockSignals(True)
        self.enabled.setChecked(self.supervisor.entries_enabled)
        self.enabled.blockSignals(False)
        status = self.supervisor.status
        if self.supervisor.provider is None:
            status = 'AI not configured. Set GEMINI_API_KEY and ATLAS_SUPERVISOR_MODEL locally, then restart.'
        self.state.setText(f'{status}\nProtection: {self.supervisor.protection_status}')
        snapshot = self.supervisor.paper.snapshot()
        self.summary.setText(f"Cash: ${Decimal(snapshot['cash']):,.2f}   Realized P/L: ${Decimal(snapshot['realized']):,.2f}   Open positions: {len(snapshot['positions'])}")
        self.close_positions.setEnabled(bool(snapshot['positions']))
        if self.title in ('Orders', 'Activity'):
            rows = snapshot['events']
            keys = ('at','symbol','action','quantity','fill','reason')
        elif self.title == 'Decisions':
            rows = self.supervisor.paper.decisions()
            keys = ('at','symbol','action','outcome','reason')
        else:
            rows = snapshot['positions']
            keys = ('symbol','quantity','entry','stop','target')
        if self.title in ('Replay', 'Strategies'):
            rows = []
            self.state.setText('Crypto replay and strategy editing are not available in this paper simulator. Use Mission Control for AI proposal controls.')
        self.content.setColumnCount(len(keys))
        self.content.setHorizontalHeaderLabels([key.replace('_',' ').title() for key in keys])
        self.content.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, key in enumerate(keys):
                self.content.setItem(i,j,QTableWidgetItem(str(row.get(key,''))))

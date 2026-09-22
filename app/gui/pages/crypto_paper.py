"""Separate crypto simulation views; equity rows are never reused."""
from decimal import Decimal
from datetime import datetime, UTC
from app.gui.pages.market_analysis import table, populate, panel
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QSplitter, QScrollArea,
)


class CryptoPaperPage(QWidget):
    def __init__(self, title, supervisor):
        super().__init__()
        self.setObjectName('assetAnalysisPage')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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
        self.enabled.setObjectName('cryptoProposalToggle')
        self.enabled.setChecked(supervisor.entries_enabled)
        self.enabled.setEnabled(supervisor.provider is not None)
        self.enabled.toggled.connect(self.set_enabled)
        layout.addWidget(self.enabled)
        self.state = QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.content = QTableWidget()
        self.content.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.content.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.content.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.content.horizontalHeader().setStretchLastSection(True)
        self.scanner = None
        self.intelligence = None
        if title == 'Mission Control':
            split = QSplitter(Qt.Orientation.Horizontal)
            left = QSplitter(Qt.Orientation.Vertical)
            left.addWidget(panel('ACTIVE POSITIONS', self.content))
            self.scanner = table(['Pair','Price','Bid','Ask','Spread %','Time-of-week RVOL','Score','Quote age (s)'])
            self.scanner.itemSelectionChanged.connect(self.show_selected_quote)
            left.addWidget(panel('CRYPTO SCANNER · 24/7 SPOT', self.scanner))
            left.setStretchFactor(0,1); left.setStretchFactor(1,1)
            split.addWidget(left)
            self.intelligence = QLabel('Select a crypto pair to inspect its current research evidence.')
            self.intelligence.setTextFormat(Qt.TextFormat.PlainText)
            self.intelligence.setWordWrap(True)
            self.intelligence.setAlignment(Qt.AlignmentFlag.AlignTop)
            scroll = QScrollArea(); scroll.setObjectName("sectionScrollArea"); scroll.setWidgetResizable(True); scroll.setWidget(self.intelligence)
            split.addWidget(panel('ATLAS CRYPTO INTELLIGENCE', scroll))
            split.setStretchFactor(0,3); split.setStretchFactor(1,2)
            layout.addWidget(split,1)
            self.quote_rows = []
        else:
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
        if self.scanner is not None:
            now = datetime.now(UTC)
            selected = self.scanner.currentRow()
            selected_symbol = self.quote_rows[selected].pair.canonical_symbol if 0 <= selected < len(self.quote_rows) else None
            all_quotes = tuple(self.supervisor.source())
            self.quote_rows = sorted(all_quotes, key=lambda row: row.rank)[:50]
            rows = []
            for quote in self.quote_rows:
                age = (now-quote.timestamp).total_seconds()
                rows.append(dict(symbol=quote.pair.canonical_symbol,price=quote.price,bid=quote.bid,ask=quote.ask,
                    spread=quote.features.spread_percent,rvol=quote.features.relative_volume_time_of_week,
                    score=quote.score,age=f'{age:.1f}' if 0 <= age <= 60 else f'STALE ({age:.1f})'))
            self.scanner.blockSignals(True)
            populate(self.scanner, rows, ('symbol','price','bid','ask','spread','rvol','score','age'))
            if selected_symbol is not None:
                for i, quote in enumerate(self.quote_rows):
                    if quote.pair.canonical_symbol == selected_symbol:
                        self.scanner.selectRow(i); break
                else:
                    self.scanner.clearSelection(); self.scanner.setCurrentCell(-1,-1)
            self.scanner.blockSignals(False)
            self.show_selected_quote()
            fresh = {q.pair.canonical_symbol:q for q in all_quotes
                     if 0 <= (now-q.timestamp).total_seconds() <= 60 and q.bid is not None}
            unrealized = Decimal('0'); marked = Decimal('0'); missing = []
            for position in snapshot['positions']:
                quote = fresh.get(position['symbol'])
                if quote is None:
                    missing.append(position['symbol']); continue
                qty=Decimal(position['quantity']); value=qty*quote.bid
                marked+=value; unrealized+=value-qty*Decimal(position['entry'])
            if missing:
                self.summary.setText(self.summary.text()+'   Equity / unrealized: unavailable (stale or missing position quotes)')
            else:
                equity=Decimal(snapshot['cash'])+marked
                self.summary.setText(self.summary.text()+f'   Bid-marked equity: ${equity:,.2f}   Unrealized: ${unrealized:,.2f}')
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

    def show_selected_quote(self):
        if self.intelligence is None:
            return
        index = self.scanner.currentRow()
        if not 0 <= index < len(self.quote_rows):
            self.intelligence.setText('Select a crypto pair. No quote freshness or execution readiness is inferred from worker status.')
            return
        q = self.quote_rows[index]
        age = (datetime.now(UTC)-q.timestamp).total_seconds()
        f = q.features
        def show(value):
            return 'Unavailable' if value is None else str(value)
        fields = [
            (q.pair.canonical_symbol, f'24/7 SPOT · {q.regime.value}'),
            ('Quote', 'FRESH' if 0 <= age <= 60 else 'STALE / INVALID TIME'),
            ('Observed at', q.timestamp.isoformat()), ('Age (seconds)', f'{age:.1f}'),
            ('Last', q.price), ('Bid / Ask', f'{show(q.bid)} / {show(q.ask)}'),
            ('Spread (%)', f.spread_percent), ('Research score', q.score),
            ('Time-of-week relative volume', f.relative_volume_time_of_week),
            ('Notional volume', f.notional_volume), ('Volume acceleration', f.volume_acceleration),
            ('Short-window acceleration', f.short_window_acceleration),
            ('Volatility', f.volatility), ('Trend velocity', f.trend_velocity),
            ('Breakout distance (%)', f.high_breakout_distance_percent),
            ('Research events', ', '.join(x.value for x in q.event_types) or 'None'),
            ('Catalyst', 'Not connected to this snapshot'),
            ('Order-book depth', 'Not supplied by this research snapshot'),
            ('Execution', 'Paper proposals require enabled AI and independent risk checks'),
            ('Marking', 'Bid marks exclude exit fees and slippage'),
        ]
        self.intelligence.setText('\n\n'.join(f'{label}\n{show(value)}' for label,value in fields))

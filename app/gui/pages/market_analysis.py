"""Asset-specific analysis workspaces; unavailable feeds never imply readiness."""
from decimal import Decimal, InvalidOperation
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFormLayout, QLineEdit, QComboBox,
    QPushButton, QSplitter, QScrollArea, QFileDialog)
from app.asset_modules.futures_ledger_view import FuturesLedgerReader


def table(headers):
    result = QTableWidget(0, len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    result.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    result.horizontalHeader().setStretchLastSection(True)
    return result


def populate(widget, rows, keys):
    widget.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, key in enumerate(keys):
            value = row.get(key)
            widget.setItem(i, j, QTableWidgetItem('—' if value is None else str(value)))


def panel(title, child):
    box = QGroupBox(title)
    box.setObjectName("assetAnalysisPanel")
    layout = QVBoxLayout(box)
    layout.addWidget(child)
    return box


def futures_scenario(entry, exit_price, tick_size, tick_value, contracts, fees, side):
    values = (entry, exit_price, tick_size, tick_value, contracts, fees)
    if any(not x.is_finite() for x in values) or min(tick_size,tick_value,contracts) <= 0 or fees < 0:
        raise ValueError('Enter finite values, positive tick size/value and contract count.')
    if contracts != contracts.to_integral_value() or side not in ('Long','Short'):
        raise ValueError('Contracts must be whole numbers.')
    if any(p % tick_size for p in (entry, exit_price)):
        raise ValueError('Prices must align to the contract tick size.')
    return (exit_price-entry)/tick_size*tick_value*contracts*(1 if side=='Long' else -1)-fees*contracts


def option_scenario(strike, premium, underlying_at_expiry, multiplier, contracts, fees, right):
    values = (strike,premium,underlying_at_expiry,multiplier,contracts,fees)
    if any(not x.is_finite() for x in values) or min(strike,multiplier,contracts)<=0 or min(premium,underlying_at_expiry,fees)<0:
        raise ValueError('Enter finite, non-negative prices and positive strike, multiplier and contracts.')
    if contracts != contracts.to_integral_value() or right not in ('Call','Put'):
        raise ValueError('Contracts must be whole numbers.')
    intrinsic = max(Decimal(0), (underlying_at_expiry-strike)*(1 if right=='Call' else -1))
    debit = premium*multiplier*contracts+fees*contracts
    return intrinsic*multiplier*contracts-debit, debit


class DerivativeWorkspace(QWidget):
    """Interactive planning is available without pretending an adapter exists."""
    def __init__(self, asset, route, *, ledger_reader=None):
        super().__init__()
        self.setObjectName('assetAnalysisPage')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        futures = asset.value == 'FUTURES'
        self.futures,self.route = futures,route
        self.ledger_reader = (ledger_reader or FuturesLedgerReader()) if futures else None
        title = asset.value.title()
        layout = QVBoxLayout(self)
        heading = QLabel(f'{asset.value} · {route.upper()}')
        heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        status = QLabel('SAVED PAPER LEDGER · No futures feed or worker connected to Atlas' if futures else
                        'ANALYSIS ONLY · Market data and paper execution adapters are not connected')
        self.status = status
        status.setWordWrap(True)
        layout.addWidget(status)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0,0,0,0)
        if futures:
            self.ledger_summary = QLabel('Saved paper account: unavailable')
            self.ledger_summary.setTextFormat(Qt.TextFormat.PlainText)
            self.ledger_summary.setWordWrap(True)
            ll.addWidget(panel('SAVED PAPER ACCOUNT',self.ledger_summary))
            controls = QHBoxLayout()
            self.open_ledger = QPushButton('Open paper ledger…')
            self.refresh_ledger = QPushButton('Refresh saved ledger')
            self.open_ledger.clicked.connect(self.choose_paper_ledger)
            self.refresh_ledger.clicked.connect(self.refresh_paper_ledger)
            controls.addWidget(self.open_ledger); controls.addWidget(self.refresh_ledger)
            ll.addLayout(controls)
            self._event_view = route in ('Orders','Activity','Decisions','Replay')
            headers = (['Time','Contract','Event','Side','Contracts','Price','Net realized P/L','Intent']
                       if self._event_view else ['Contract','Side','Contracts','Entry','Stop','Target','Reserved margin','Reserved risk','Expiry'])
        elif route in ('Orders','Activity','Decisions','Replay'):
            headers = ['Time','Contract','Action','Quantity','Status','Reason']
        elif route == 'Positions':
            headers = ['Contract','Side','Contracts','Entry','Mark','P/L','Margin' if futures else 'Delta exposure']
        elif futures:
            headers = ['Contract','Expiry','Bid','Ask','Volume','Open interest','Tick size','Tick value','Initial margin','Quote time']
        else:
            headers = ['Underlying','Expiry','Strike','Right','Bid','Ask','Volume','Open interest','IV','Delta','Gamma','Theta','Vega','Multiplier','Quote time']
        self.content = table(headers)
        ll.addWidget(panel(('SAVED SIMULATOR EVENTS' if self._event_view else 'SAVED PAPER POSITIONS')
                          if futures else 'OPTION CHAIN', self.content), 1)
        empty = QLabel('No connected feed. No orders or positions are inferred from equity data.')
        self.ledger_notice = empty
        empty.setTextFormat(Qt.TextFormat.PlainText)
        empty.setWordWrap(True); ll.addWidget(empty)
        splitter.addWidget(left)
        right = QWidget(); rl = QVBoxLayout(right)
        text = ('Contract expiry and roll dates, tick value, session, volume, open interest, and current broker margin are required. Margin is collateral, not a loss limit.' if futures else
                'Use the exact contract expiry, strike, call/put, multiplier and deliverable. Evaluate bid/ask liquidity, IV and Greeks together with the underlying. Exercise and assignment rules depend on the contract.')
        info = QLabel(text); info.setWordWrap(True); rl.addWidget(panel('TRADE INTELLIGENCE',info))
        formbox = QWidget(); form = QFormLayout(formbox)
        self.inputs = {}
        fields = ([('entry','Entry'),('exit','Scenario exit'),('tick_size','Tick size'),('tick_value','Tick value ($)'),('contracts','Contracts'),('fees','Round-trip fees / contract ($)')] if futures else
                  [('strike','Strike'),('premium','Premium / unit'),('underlying','Underlying at expiry'),('multiplier','Contract multiplier'),('contracts','Contracts'),('fees','Total fees / contract ($)')])
        for key,label in fields:
            field=QLineEdit(); field.setPlaceholderText('Enter contract value'); self.inputs[key]=field; form.addRow(label,field)
        self.direction=QComboBox(); self.direction.addItems(['Long','Short'] if futures else ['Call','Put'])
        form.addRow('Direction' if futures else 'Long option',self.direction)
        self.calculate=QPushButton('Calculate scenario'); self.calculate.setObjectName('secondaryButton')
        self.calculate.clicked.connect(self.recalculate); form.addRow(self.calculate)
        self.result=QLabel('Enter your scenario. This does not submit an order.'); self.result.setWordWrap(True); form.addRow(self.result)
        rl.addWidget(panel('P/L SCENARIO' if futures else 'LONG OPTION · EXPIRY SCENARIO',formbox))
        notes=QLabel('Scenario excludes slippage, liquidation and changing margin.' if futures else 'Expiry payoff only; not a pre-expiry valuation. Long calls/puts only. No short options, spreads, exercise or assignment simulation.')
        notes.setWordWrap(True); rl.addWidget(notes)
        link=('https://www.cmegroup.com/education/courses/introduction-to-futures/calculating-futures-contract-profit-or-loss' if futures else 'https://www.optionseducation.org/advancedconcepts/volatility-the-greeks')
        source=QLabel(f'<a href="{link}">{"CME contract P/L reference" if futures else "OIC volatility and Greeks reference"}</a>'); source.setOpenExternalLinks(True); rl.addWidget(source); rl.addStretch()
        scroll=QScrollArea(); scroll.setObjectName("sectionScrollArea"); scroll.setWidgetResizable(True); scroll.setWidget(right); splitter.addWidget(scroll)
        splitter.setStretchFactor(0,3);splitter.setStretchFactor(1,2)
        layout.addWidget(splitter,1)
        if futures: self.refresh_paper_ledger()

    def choose_paper_ledger(self):
        path,_ = QFileDialog.getOpenFileName(self,'Open saved futures paper ledger',
            str(self.ledger_reader.path or ''),'SQLite ledgers (*.sqlite3 *.db);;All files (*)')
        if path:
            self.ledger_reader.select(path)
            self.refresh_paper_ledger()

    def refresh_paper_ledger(self):
        if not self.futures: return
        view = self.ledger_reader.read()
        self.last_ledger_view = view
        self.ledger_notice.setText(f'{view.notice}\nFile: {view.path or "Not selected"}\nRead: {view.read_at}\nRefresh to reread saved records.')
        if view.status!='AVAILABLE':
            self.ledger_summary.setText('Saved paper account unavailable · inventory unknown')
            self.content.setRowCount(0)
            return
        self.ledger_summary.setText(
            f'Cash: {view.cash} · Realized P/L: {view.realized_pnl}\n'
            f'Reserved margin: {view.reserved_margin} · Reserved risk: {view.reserved_risk}\n'
            f'Margin headroom after exit fees: {view.margin_headroom}\n'
            'Saved simulator balances; no unrealized P/L or broker buying power.')
        if self._event_view:
            populate(self.content,view.events,('time','contract','action','side','quantity','price','net_pnl','intent'))
        else:
            populate(self.content,view.positions,('contract','side','quantity','entry','stop','target','margin','risk','expiry'))

    def showEvent(self,event):
        super().showEvent(event)
        if self.futures: self.refresh_paper_ledger()

    def recalculate(self):
        try:
            v={k:Decimal(w.text().strip()) for k,w in self.inputs.items()}
            if self.futures:
                result=futures_scenario(v['entry'],v['exit'],v['tick_size'],v['tick_value'],v['contracts'],v['fees'],self.direction.currentText())
                text=f'Scenario P/L after entered fees: ${result:,.2f}'
            else:
                result, debit=option_scenario(v['strike'],v['premium'],v['underlying'],v['multiplier'],v['contracts'],v['fees'],self.direction.currentText())
                text=f'Expiry P/L: ${result:,.2f}\nPremium plus fees at risk: ${debit:,.2f}'
            self.result.setText(text)
        except (InvalidOperation,ValueError,ArithmeticError) as error:
            self.result.setText(str(error) if isinstance(error,ValueError) else 'Enter a valid number in every field.')

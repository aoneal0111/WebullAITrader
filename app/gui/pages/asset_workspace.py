"""Equity workstation components with market-specific, read-only data adapters."""
from queue import Empty, Queue
from threading import Thread
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QStackedWidget, QFileDialog)
from app.assets import AssetType
from app.gui.pages.dashboard import DashboardPage
from app.gui.pages.orders import OrdersPage
from app.gui.pages.placeholder import PlaceholderPage
from app.gui.pages.replay import ReplayPage
from app.gui.widgets.positions_panel import PositionsPanel
from app.gui.widgets.activity_panel import ActivityPanel
from app.gui.widgets.decisions_panel import DecisionsPanel
from app.gui.widgets.watchlist_panel import WatchlistPanel
from app.gui.widgets.global_status_bar import GlobalStatusBar
from app.gui.models import RuntimeSnapshot, DecisionsSnapshot
from app.gui.models.runtime import RuntimeState
from app.gui.projections.asset_workspace import crypto_view, derivative_view, text


class AssetWorkspace(QWidget):
    """One persistent set of Equity widgets per market; selection never starts it."""
    view_changed = Signal(object)

    def __init__(self, asset, *, reader=None, controller=None, version='0.1.0'):
        super().__init__()
        self.asset=asset;self.busy=False;self.results=Queue(maxsize=1);self.last_report=None
        self.setObjectName('appRoot')
        if reader is None:
            if asset is AssetType.CRYPTO:
                from app.strategy_lab.crypto_monitor import CryptoMonitorReader
                reader=CryptoMonitorReader()
            elif asset is AssetType.FUTURES:
                from app.asset_modules.futures_ledger_view import FuturesLedgerReader
                reader=FuturesLedgerReader()
            else:
                from app.asset_modules.options_ledger_view import OptionsLedgerReader
                reader=OptionsLedgerReader()
        self.reader=reader
        self.controller=controller
        if asset is AssetType.CRYPTO and controller is None:
            from app.strategy_lab.crypto_monitor import CryptoMonitorReader
            if isinstance(reader,CryptoMonitorReader):
                from app.strategy_lab.crypto_workspace_control import CryptoWorkspaceControl
                self.controller=CryptoWorkspaceControl(reader)
        self.command_busy=False;self.command_results=Queue(maxsize=1);self.read_generation=0;self._rendered_wallet=None
        root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0);root.setSpacing(6)
        source=QHBoxLayout();self.source_label=QLabel(f'{asset.value.title()} · PAPER account')
        self.source_label.setTextFormat(Qt.TextFormat.PlainText);source.addWidget(self.source_label)
        self.wallet=QComboBox();self.wallet.setMinimumWidth(260)
        self.wallet.setVisible(asset is AssetType.CRYPTO);self.wallet.currentIndexChanged.connect(self.render_cached)
        source.addWidget(self.wallet);source.addStretch()
        self.open_ledger=QPushButton('Open saved ledger…');self.open_ledger.clicked.connect(self.choose_ledger);self.open_ledger.setObjectName('secondaryButton')
        self.open_ledger.setVisible(asset is not AssetType.CRYPTO);source.addWidget(self.open_ledger)
        self.refresh_button=QPushButton('Refresh');self.refresh_button.clicked.connect(self.refresh);self.refresh_button.setObjectName('secondaryButton');source.addWidget(self.refresh_button)
        root.addLayout(source)
        self.control_message=QLabel('');self.control_message.setWordWrap(True)
        self.control_message.setTextFormat(Qt.TextFormat.PlainText);root.addWidget(self.control_message)
        self.pages=QStackedWidget();root.addWidget(self.pages,1)
        self.dashboard=DashboardPage();ws=self.dashboard.market_workspace
        ws.warrior_enrichment=False
        ws.trade_intelligence.native_strategy=True
        if asset is AssetType.CRYPTO:
            ws.watchlist.classification_aliases={'Qualifying':'SOURCE_BUY_SIGNAL','Near Miss':'NO_SOURCE_BUY_SIGNAL','Watching':'STALE'}
            for key,label in (('Qualifying','Buy signals'),('Near Miss','No buy signal'),('Watching','Stale')):
                ws.watchlist._view_buttons[key].setText(label)
            ws.watchlist._table.setColumnWidth(1,105)
            ws.watchlist._table.setColumnWidth(2,115)
        ws.crypto_research._timer.stop();ws.crypto_scanner_section.hide()
        ws.opportunities_section.heading.setText(f'{asset.value} SCANNER')
        # Share the exact account/risk panel, but parent it outside the page
        # stack so every sidebar route keeps the same bottom account cards.
        self.footer=ws.portfolio_section;ws.layout().removeWidget(self.footer)
        self.footer.setParent(self)
        self.dashboard.entry_admission_notice.setTextFormat(Qt.TextFormat.PlainText)
        from app.gui.projections.asset_workspace import price
        self.positions=PositionsPanel();self.orders=OrdersPage(
            price_formatter=(lambda value:price(value,currency=False)) if asset is AssetType.CRYPTO else None)
        self.activity=ActivityPanel();self.decisions=DecisionsPanel();self.scanner=WatchlistPanel();self.replay=ReplayPage()
        if asset is AssetType.CRYPTO:
            self.scanner._table.setColumnWidth(1,110);self.scanner._table.setColumnWidth(3,130)
        self.settings=QWidget();settings_layout=QVBoxLayout(self.settings)
        self.source_detail=QLabel('Waiting for a saved account snapshot.');self.source_detail.setWordWrap(True)
        self.source_detail.setTextFormat(Qt.TextFormat.PlainText);settings_layout.addWidget(self.source_detail);settings_layout.addStretch()
        self.operator=self.dashboard.operator_workspace
        for p in (self.dashboard,self.positions,self.orders,
            PlaceholderPage('Strategies','Strategy activity requires evidence from this market’s worker.'),
            self.settings,self.activity,self.decisions,self.scanner,self.replay,self.operator):self.pages.addWidget(p)
        self.status_bar=GlobalStatusBar(version=version)
        self.status_bar.setParent(self)
        self.status_bar.set_market_context(f'{asset.value.title()} · PAPER source · Read only')
        ws.positions_panel.selected_symbol_changed.connect(self.show_position)
        self.positions.selected_symbol_changed.connect(self.show_position)
        for label in ws.trade_intelligence.findChildren(QLabel):
            value=label.text()
            if 'WARRIOR' in value:label.setText(value.replace('WARRIOR MOMENTUM','STRATEGY').replace('WARRIOR','STRATEGY'))
        for button in (ws.runtime_controls.start_button,ws.runtime_controls.stop_button,
                ws.runtime_controls.emergency_stop_button,self.replay.play_button,
                self.replay.pause_button,self.replay.step_button,self.replay.restart_button,
                self.replay.timestamp_button,self.replay.event_index_button):
            button.setEnabled(False);button.setToolTip('This market view reads existing PAPER evidence; no execution adapter is connected to this control.')
        if self.controller is not None:
            ws.runtime_controls.start_button.clicked.connect(lambda:self.command('start'))
            ws.runtime_controls.stop_button.clicked.connect(lambda:self.command('stop'))
            ws.runtime_controls.emergency_stop_requested.connect(lambda:self.command('emergency'))
            self.status_bar.set_market_context('Crypto · PAPER worker controls · selected wallet')
        self.dashboard.runtime_header.settings_button.hide();self.dashboard.runtime_header.menu_button.hide()
        self.timer=QTimer(self);self.timer.setInterval(250);self.timer.timeout.connect(self.poll)
        self.refresh_timer=QTimer(self);self.refresh_timer.setInterval(5000);self.refresh_timer.timeout.connect(self.refresh)
        self.render_view(crypto_view(None,None) if asset is AssetType.CRYPTO else None)

    def set_route(self,route):self.pages.setCurrentIndex(route)

    def choose_ledger(self):
        if self.busy:return
        name,_=QFileDialog.getOpenFileName(self,'Open saved PAPER ledger',str(getattr(self.reader,'path','') or ''),'SQLite (*.sqlite3 *.db);;All files (*)')
        if name:self.reader.select(name);self.refresh()

    def showEvent(self,event):
        super().showEvent(event);self.timer.start();self.refresh_timer.start()
        self.status_bar._clock.start(1000);self.dashboard.runtime_header._clock.start(1000)
        self.refresh()

    def hideEvent(self,event):
        self.timer.stop();self.refresh_timer.stop()
        self.status_bar._clock.stop();self.dashboard.runtime_header._clock.stop()
        super().hideEvent(event)

    def shutdown(self):
        self.timer.stop();self.refresh_timer.stop()
        self.status_bar._clock.stop();self.dashboard.runtime_header._clock.stop()
        self.dashboard.market_workspace.crypto_research._timer.stop()

    def closeEvent(self,event):
        self.shutdown();super().closeEvent(event)

    def refresh(self):
        if self.busy:return
        self.busy=True;self.refresh_button.setEnabled(False);self.open_ledger.setEnabled(False)
        reader,queue,generation=self.reader,self.results,self.read_generation
        def work():
            try:queue.put((reader.read(),None,generation))
            except Exception as error:queue.put((None,type(error).__name__,generation))
        Thread(target=work,name='market-workspace-read-only',daemon=True).start()

    def command(self,action):
        if self.command_busy or self.controller is None:return
        work=self.wallet.currentData()
        if action!='emergency' and not work:return
        self.command_busy=True;self.update_controls()
        self.control_message.setText(('Stopping all discovered crypto PAPER workers…' if action=='emergency'
            else ('Starting ' if action=='start' else 'Stopping ')+self.wallet.currentText()+'…')+
            ' Holdings are retained. Equity is separate.')
        controller,queue=self.controller,self.command_results
        def work_command():
            try:queue.put(controller.execute(action,work))
            except Exception as error:queue.put(dict(success=False,results=[dict(error=type(error).__name__)]))
        Thread(target=work_command,name='crypto-paper-control',daemon=True).start()

    def update_controls(self):
        controls=self.dashboard.market_workspace.runtime_controls
        available=self.controller is not None and not self.command_busy and self.wallet.count()>0
        bot=None
        if self.last_report and self.asset is AssetType.CRYPTO:
            bot=next((b for b in self.last_report.get('engines',[]) if b.get('work')==self.wallet.currentData()),None)
        running=bot.get('container_running') if bot else None
        controls.start_button.setText('START WALLET')
        controls.stop_button.setText('STOPPING…' if self.command_busy else 'STOP WALLET')
        controls.emergency_stop_button.setText('STOP ALL CRYPTO')
        controls.start_button.setEnabled(available and running is False)
        controls.stop_button.setEnabled(available and running is True)
        controls.emergency_stop_button.setEnabled(available)
        controls.start_button.setToolTip('Start the selected existing PAPER container and resume entries in its saved wallet.')
        controls.stop_button.setToolTip('Stop the selected crypto worker and verify Docker stopped it. Holdings retained; exit management stops.')
        controls.emergency_stop_button.setToolTip('Stop all discovered managed crypto PAPER workers. Does not sell holdings or stop equity.')

    def poll(self):
        try:command=self.command_results.get_nowait()
        except Empty:command=None
        if command is not None:
            self.command_busy=False;self.read_generation+=1
            if self.last_report:
                for result in command.get('results',[]):
                    if result.get('success'):
                        for bot in self.last_report.get('engines',[]):
                            if bot.get('work')==result.get('work'):
                                bot['container_running']=result.get('state')=='RUNNING'
                self.render_cached()
            details='; '.join(str(r.get('error') or r.get('state',''))+(' · '+r['warning'] if r.get('warning') else '') for r in command.get('results',[]))
            self.control_message.setText(('Crypto command verified: ' if command.get('success') else 'CRYPTO COMMAND FAILED: ')+
                details+'. Holdings retained. Stopped workers do not manage exits.')
            self.update_controls();self.refresh()
        try:result=self.results.get_nowait()
        except Empty:return
        report,error=result[:2]
        generation=result[2] if len(result)>2 else self.read_generation
        self.busy=False;
        if generation!=self.read_generation:
            self.refresh();return
        self.refresh_button.setEnabled(True);self.open_ledger.setEnabled(True)
        if error:
            from app.gui.projections.asset_workspace import MarketView
            self.last_report=None;self.render_view(MarketView('Account read failed: '+error,state='FAILED'))
            return
        self.last_report=report
        if self.asset is AssetType.CRYPTO:
            selected=self.wallet.currentData();self.wallet.blockSignals(True);self.wallet.clear()
            for index,bot in enumerate(report.get('engines',())):
                self.wallet.addItem(text(bot.get('label')),bot.get('work',bot.get('container',str(index))))
            idx=self.wallet.findData(selected);self.wallet.setCurrentIndex(max(0,idx));self.wallet.blockSignals(False)
        self.render_cached()

    def render_cached(self,*_):
        if self.last_report is None:return
        if self.asset is AssetType.CRYPTO:
            bots=self.last_report.get('engines',[]);index=self.wallet.currentIndex()
            view=crypto_view(bots[index] if 0<=index<len(bots) else None,self.last_report.get('checked_at'))
        else:view=derivative_view(self.last_report,self.asset.value)
        self.render_view(view)
        if self.asset is AssetType.CRYPTO and 0<=index<len(bots):
            import json
            settings=(bots[index].get('workspace_telemetry') or {}).get('strategy_settings',{})
            self.source_detail.setText(view.notice+'\n\nNative strategy settings:\n'+json.dumps(settings,indent=2,default=str))

    def render_view(self,view):
        if view is None:
            from app.gui.projections.asset_workspace import MarketView
            view=MarketView(f'{self.asset.value.title()} · No saved PAPER ledger selected. No feed or worker connected.')
        self.view=view;ws=self.dashboard.market_workspace
        self.footer.heading.setText('ACCOUNT / RISK · '+(self.wallet.currentText() if self.asset is AssetType.CRYPTO and self.wallet.count() else self.asset.value))
        ws.portfolio_summary.render(view.portfolio)
        header=self.dashboard.runtime_header
        state=RuntimeState(view.state) if view.state in RuntimeState._value2member_map_ else RuntimeState.STOPPED
        header.render(RuntimeSnapshot('PAPER',state,view.broker,view.feed,'NOT CONNECTED',False,'--',0,view.notice))
        header.render_portfolio(view.portfolio);header._set('Scanner',view.scanner.scanner_status)
        header._set('Runtime',view.state)
        ws.runtime_controls.set_runtime_status('PAPER',view.state)
        self.source_label.setText(f'{self.asset.value.title()} · PAPER · {view.state}')
        self.source_label.setToolTip(view.notice)
        self.dashboard.entry_admission_notice.setText(view.notice);self.source_detail.setText(view.notice)
        for panel in (ws.positions_panel,self.positions):
            panel.set_runtime_phase(state,account_loaded=dict(view.portfolio.metrics).get('Open Positions')!='--',
                positions_synchronized=dict(view.portfolio.metrics).get('Open Positions')!='--',positions_status='SAVED PAPER')
            panel.render(view.positions)
        ws.orders_panel.render(view.orders);self.orders.render_projection(view.order_models)
        self.activity.render(view.activity);self.dashboard.operations_activity_panel.render(view.activity)
        self.operator.positions.render(view.positions);self.operator.orders.render(view.orders)
        self.operator.portfolio_intelligence.render(view.portfolio)
        self.decisions.render(DecisionsSnapshot());self.dashboard.decisions_panel.render(DecisionsSnapshot())
        self.scanner.render(view.scanner);ws.render(view.scanner)
        ws.trade_intelligence._lifecycle_empty.setText(view.notice)
        selected=ws.positions_panel.selected_symbol
        symbol=selected if any(r.symbol==selected for r in view.positions.management) else view.positions.management[0].symbol if view.positions.management else None
        if self.asset is AssetType.CRYPTO and self.wallet.currentData()!=self._rendered_wallet:
            self._rendered_wallet=self.wallet.currentData()
            ws.positions_panel.activity_tabs.setCurrentIndex(0 if view.positions.rows else 2)
        self.show_position(symbol)
        self.status_bar.runtime.set_status(self.asset.value.title()+' '+view.state,'good' if state is RuntimeState.RUNNING else 'neutral')
        self.status_bar.data_feed.set_status('Feed '+view.feed,'neutral')
        self.status_bar.broker.set_status(view.broker,'neutral')
        self.status_bar.ai.set_status('AI not reported','neutral')
        if dict(view.portfolio.metrics).get('Open Positions')=='--':
            for panel in (ws.positions_panel,self.positions):
                panel._symbol.setText('POSITION DATA UNAVAILABLE')
                panel._position_state.setText('NOT LOADED')
                panel._protection_status.setText('NOT EVIDENCED')
                panel._protection_detail.setText('No account inventory snapshot available')
        if self.asset is AssetType.CRYPTO:self.update_controls()
        self.view_changed.emit(view)

    def show_position(self,symbol):
        if not hasattr(self,'view'):return
        row=next((r for r in self.view.positions.management if r.symbol==symbol),None)
        panel=self.dashboard.market_workspace.trade_intelligence
        if row is not None:
            from dataclasses import replace
            from app.gui.models import WatchlistRow
            market=next((r for r in self.view.scanner.rows if r.symbol==symbol),None)
            if market is not None:
                panel.render(replace(market,selected=True,latest_price=row.mark))
            else:
                panel.render(WatchlistRow(symbol=row.symbol,selected=True,latest_price=row.mark,change='--',
                    change_percent='--',bid='--',ask='--',volume='--',market_status='HELD PAPER',
                    last_update=row.updated_at,stale=row.management_state,strategy_name=row.strategy,
                    explanations=row.management_state,freshness=row.management_state))
            for positions in (self.dashboard.market_workspace.positions_panel,self.positions):
                blocked=positions.blockSignals(True)
                try:
                    positions.select_symbol(symbol);positions.render(self.view.positions)
                finally:positions.blockSignals(blocked)
        panel.render_active_position(row,symbol=symbol)

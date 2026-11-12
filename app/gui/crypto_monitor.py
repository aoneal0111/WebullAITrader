"""Independent read-only window for the Docker crypto PAPER experiments."""
import argparse
import json
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

from app.strategy_lab.crypto_monitor import CryptoMonitorReader


def display(value):
    if value is None:return '—'
    if isinstance(value,bool):return 'Yes' if value else 'No'
    if isinstance(value,float):return f'{value:,.4f}'
    return str(value)[:300]


def create_window(reader=None, parent=None):
    # Keep the --check path usable without Qt or the Atlas trading runtime.
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QLabel,
        QTabWidget, QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView)
    from app.gui.design.theme import application_stylesheet

    class MonitorWindow(QMainWindow):
        def __init__(self):
            super().__init__(parent, Qt.WindowType.Window)
            self.setWindowTitle('Atlas · Crypto PAPER bots · Read only')
            self.resize(1250,720)
            self.setStyleSheet(application_stylesheet())
            self.reader=reader or CryptoMonitorReader()
            self.results=Queue(maxsize=1);self.busy=False
            body=QWidget();layout=QVBoxLayout(body);self.setCentralWidget(body)
            note=QLabel('CRYPTO PAPER · Separate simulated wallets · Open positions are not wins. '
                'Current unrealized P/L is unavailable. This window does not start or stop trading.')
            note.setWordWrap(True);note.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(note)
            self.health=QLabel('Reading local crypto status…');self.health.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(self.health)
            self.tabs=QTabWidget();layout.addWidget(self.tabs)
            self.tables={}
            definitions={
                'Bots': ['Wallet / strategy','Checkpoint health','Container','Age (s)','Fresh pairs',
                    'Entries paused','Open','Closed','Win rate','Closed net ($)','Working orders','Source conditions'],
                'Open positions':['Wallet / strategy','Pair','Amount','Entry price','Entry time'],
                'Closed trades':['Wallet / strategy','Pair','Amount','Entry','Exit','Net ($)','Closed at','Exit reason'],
                'Working orders':['Wallet / strategy','Pair','Side','Type','Status','Price','Amount','Filled','Remaining']}
            for title,headers in definitions.items():
                table=QTableWidget(0,len(headers));table.setHorizontalHeaderLabels(headers)
                table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
                table.verticalHeader().hide();self.tables[title]=table;self.tabs.addTab(table,title)
            self.timer=QTimer(self);self.timer.setInterval(250);self.timer.timeout.connect(self.poll)
            self.refresh_timer=QTimer(self);self.refresh_timer.setInterval(5000)
            self.refresh_timer.timeout.connect(self.refresh)

        def showEvent(self,event):
            super().showEvent(event);self.timer.start();self.refresh_timer.start();self.refresh()

        def closeEvent(self,event):
            self.timer.stop();self.refresh_timer.stop();super().closeEvent(event)

        def refresh(self):
            if self.busy:return
            self.busy=True
            reader,queue=self.reader,self.results
            def work():
                try:queue.put((reader.read(),None))
                except Exception as error:queue.put((None,type(error).__name__))
            Thread(target=work,name='crypto-read-only-monitor',daemon=True).start()

        def poll(self):
            try:report,error=self.results.get_nowait()
            except Empty:return
            self.busy=False
            if error:self.health.setText('Read failed: '+error+' · Trading bots were not changed.');return
            self.render_report(report)

        def render_report(self,report):
            rows={key:[] for key in self.tables}
            for bot in report['engines']:
                label=bot['label'];rate=bot.get('closed_win_rate')
                states=bot.get('source_states') or {}
                conditions=', '.join(f'{k}: {v}' for k,v in list(states.items())[:8])
                if bot.get('read_error'):conditions='Read error: '+bot['read_error']
                rows['Bots'].append([label,bot['health'],bot.get('container_state','Not verified'),
                    bot.get('checkpoint_age_seconds'),bot.get('fresh_ready_pairs'),bot.get('pause_new_entries'),
                    bot.get('open_positions'),bot.get('closed_trades'),f'{rate:.1%}' if rate is not None else None,
                    bot.get('closed_lifecycle_net_usd'),bot.get('working_orders'),conditions])
                for item in bot.get('holdings',[]):
                    rows['Open positions'].append([label,*[item.get(k) for k in ('pair','amount','open_rate','open_date')]])
                for item in bot.get('closed',[]):
                    rows['Closed trades'].append([label,*[item.get(k) for k in ('pair','amount','open_rate','close_rate','close_profit_abs','close_date','exit_reason')]])
                for item in bot.get('orders',[]):
                    rows['Working orders'].append([label,*[item.get(k) for k in ('symbol','ft_order_side','order_type','status','price','amount','filled','remaining')]])
            for title,table in self.tables.items():
                table.setRowCount(len(rows[title]))
                for i,row in enumerate(rows[title]):
                    for j,value in enumerate(row):table.setItem(i,j,QTableWidgetItem(display(value)))
            self.health.setText(('Updated '+report['checked_at']+' · '+str(len(report['engines']))+' paper wallets'
                if report['engines'] else 'No prepared crypto wallets found in '+str(self.reader.base))+
                ' · Checkpoint health and Docker process state are separate checks.')

    return MonitorWindow()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path);parser.add_argument('--check',action='store_true')
    parser.add_argument('--no-docker',action='store_true')
    args=parser.parse_args()
    options={'container_reader':lambda names:{}} if args.no_docker else {}
    reader=CryptoMonitorReader(args.base,**options)
    if args.check:
        print(json.dumps(reader.read(),indent=2,allow_nan=False));return
    from PySide6.QtWidgets import QApplication
    app=QApplication.instance() or QApplication([])
    window=create_window(reader);window.show();app.exec()


if __name__=='__main__':main()

"""Windows native Qt widget using the same quota collectors as Linux."""
import logging
import os
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QSize, QLockFile
from PySide6.QtGui import QIcon, QPainter, QColor, QPen, QAction
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QListWidget, QListWidgetItem, QDialog, QFormLayout,
    QCheckBox, QSlider, QSystemTrayIcon, QMenu, QMessageBox)
from app_paths import ROOT, DATA, WINDOWS
from collector import collect, read_json
from scripts.statusline_bridge import write_json
from usage_state import UsageState
import remote_usage
import windows_desktop as desktop

NAMES={'codex':'Codex','claude':'Claude Code','antigravity':'Antigravity'}
COLORS={'codex':'#b4dbc8','claude':'#e4ad94','antigravity':'#a4baff'}


def age(stamp):
    if not stamp: return '수신 없음'
    n=max(0,int((time.time()-stamp)/60))
    return '방금' if n==0 else f'{n}분 전' if n<60 else f'{n//60}시간 전'


class Fetch(QThread):
    ready=Signal(object)
    def __init__(self, previous, remote, parent):
        super().__init__(parent); self.previous=previous; self.remote=remote
    def run(self):
        try:
            if self.remote:
                with ThreadPoolExecutor(max_workers=3) as pool: list(pool.map(remote_usage.query,NAMES))
            self.ready.emit(collect(self.previous))
        except Exception:
            logging.exception('Refresh failed'); self.ready.emit(None)


class Glass(QWidget):
    def __init__(self, fixture=None):
        super().__init__()
        self.fixture=fixture
        self.setWindowTitle('AI Glass')
        self.setWindowIcon(QIcon(str(ROOT/'assets/widget.png')))
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        try: self.settings=read_json(DATA/'settings.json',{})
        except (OSError,ValueError): self.settings={}
        self.history=UsageState(DATA/'history.json')
        self.snapshot=None; self.selected=None; self.worker=None; self.last_remote=0
        self.setFixedWidth(460); self.drag_offset=None
        self.setStyleSheet('''QWidget {color:#e4eaf3;font-family:"Segoe UI";font-size:12px;}
            QPushButton {background:transparent;border:0;border-radius:9px;padding:7px;}
            QPushButton:hover,QPushButton:checked {background:rgba(170,196,230,35);}
            QPushButton:focus {border:1px solid #a4baff;}
            QLineEdit,QListWidget {background:rgba(12,17,26,65);border:1px solid #485367;border-radius:7px;padding:6px;}
            QListWidget::item {padding:8px;} QListWidget::item:selected {background:#3d4b62;}
            QDialog {background:#273040;} QToolTip {background:#273040;color:white;}''')
        root=QVBoxLayout(self); root.setContentsMargins(18,14,18,14); root.setSpacing(12)
        top=QHBoxLayout(); logo=QLabel();logo.setPixmap(self.windowIcon().pixmap(25,25));top.addWidget(logo)
        title=QLabel('AI Glass');title.setStyleSheet('font-size:16px;font-weight:600;');top.addWidget(title);top.addStretch()
        self.pin=self.button('◇','항상 위 표시',self.toggle_pin,top)
        self.button('⚙','설정',self.preferences,top)
        self.button('−','트레이로 숨기기',self.hide,top)
        self.button('×','종료',self.close,top);root.addLayout(top)
        hint=QLabel('남은 사용량');hint.setStyleSheet('color:#a8b5c7');root.addWidget(hint)
        cards=QHBoxLayout();self.cards={}
        for name,label in NAMES.items():
            card=QPushButton();card.setCheckable(True);card.setMinimumHeight(166)
            card.setAccessibleName(label+' 사용량 및 세션');card.clicked.connect(lambda _,n=name:self.select(n))
            layout=QVBoxLayout(card);layout.setContentsMargins(4,10,4,10)
            icon=QLabel();icon.setAlignment(Qt.AlignCenter)
            icon.setPixmap(QIcon(str(ROOT/'assets'/('antigravity.png' if name=='antigravity' else name+'.svg'))).pixmap(34,34))
            text=QLabel(label);text.setAlignment(Qt.AlignCenter);text.setStyleSheet('font-weight:600;')
            amount=QLabel('—');amount.setAlignment(Qt.AlignCenter);amount.setWordWrap(True)
            status=QLabel('수신 없음');status.setAlignment(Qt.AlignCenter);status.setWordWrap(True)
            status.setStyleSheet('font-size:11px;color:#a8b5c7;')
            for w in (icon,text,amount,status):layout.addWidget(w)
            cards.addWidget(card,1);self.cards[name]=(card,amount,status)
        root.addLayout(cards)
        self.detail=QWidget();detail=QVBoxLayout(self.detail);detail.setContentsMargins(0,0,0,0)
        self.quota=QLabel();self.quota.setWordWrap(True);detail.addWidget(self.quota)
        self.search=QLineEdit();self.search.setPlaceholderText('세션 제목 · 프로젝트 검색');self.search.textChanged.connect(self.render_sessions);detail.addWidget(self.search)
        filters=QHBoxLayout();self.recent=QCheckBox('최근 기록 포함');self.recent.toggled.connect(self.render_sessions);filters.addWidget(self.recent)
        self.favorites=QCheckBox('즐겨찾기만');self.favorites.toggled.connect(self.render_sessions);filters.addWidget(self.favorites);detail.addLayout(filters)
        self.sessions=QListWidget();self.sessions.setMinimumHeight(200);self.sessions.itemClicked.connect(self.open_session);detail.addWidget(self.sessions)
        actions=QHBoxLayout();self.button('☆ 즐겨찾기','선택한 세션 즐겨찾기 전환',self.favorite,actions)
        self.button('CLI 열기','에이전트 터미널 열기',lambda:self.safe(lambda:desktop.open_terminal(self.selected)),actions)
        detail.addLayout(actions);root.addWidget(self.detail);self.detail.hide()
        bottom=QHBoxLayout();self.status=QLabel('15초마다 기록 확인');self.status.setWordWrap(True);bottom.addWidget(self.status,1)
        self.refresh_button=self.button('↻','서버에서 최신 사용량 조회',lambda:self.refresh(True),bottom);root.addLayout(bottom)
        self.tray=QSystemTrayIcon(self.windowIcon(),self);self.tray.setToolTip('AI Glass')
        menu=QMenu();menu.addAction('표시 / 숨김',self.toggle);menu.addAction('사용량 갱신',lambda:self.refresh(True));menu.addAction('종료',self.close)
        self.tray.setContextMenu(menu);self.tray.activated.connect(lambda reason:self.toggle() if reason==QSystemTrayIcon.Trigger else None)
        if not fixture:self.tray.show()
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh)
        if not fixture:self.timer.start(15000)
        self.apply_pin();self.restore_position();self.refresh()

    def button(self,text,tip,callback,layout):
        button=QPushButton(text);button.setToolTip(tip);button.setAccessibleName(tip)
        button.clicked.connect(lambda _:callback());layout.addWidget(button);return button

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(28,34,47,int(self.settings.get('opacity',75)*2.55)))
        painter.setPen(QPen(QColor(190,205,232,90),1));painter.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),22,22)

    def safe(self,action):
        try: action()
        except Exception as error: self.status.setText(str(error))

    def save(self):
        self.settings['position']={'x':self.x(),'y':self.y()}
        write_json(DATA/'settings.json',self.settings)

    def restore_position(self):
        p=self.settings.get('position',{});self.move(p.get('x',80),p.get('y',80));self.clamp()

    def clamp(self):
        area=self.screen().availableGeometry();x=max(area.left(),min(self.x(),area.right()-self.width()+1));y=max(area.top(),min(self.y(),area.bottom()-self.height()+1))
        if self.settings.get('snap',True):
            if abs(x-area.left())<24:x=area.left()
            if abs(x+self.width()-area.right()-1)<24:x=area.right()-self.width()+1
            if abs(y-area.top())<24:y=area.top()
        self.move(x,y)

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton and event.position().y()<60 and not self.settings.get('locked',False):
            self.drag_offset=event.globalPosition().toPoint()-self.pos()
    def mouseMoveEvent(self,event):
        if self.drag_offset is not None:self.move(event.globalPosition().toPoint()-self.drag_offset)
    def mouseReleaseEvent(self,event):
        if self.drag_offset is not None:self.drag_offset=None;self.clamp();self.safe(self.save)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key_Escape:self.select(None)

    def apply_pin(self):
        self.setWindowFlag(Qt.WindowStaysOnTopHint,self.settings.get('pinned',True));self.pin.setText('◆' if self.settings.get('pinned',True) else '◇')
    def toggle_pin(self):
        self.settings['pinned']=not self.settings.get('pinned',True);self.apply_pin();self.show();self.safe(self.save)
    def toggle(self):
        if self.isVisible():self.hide()
        else:self.show();self.raise_();self.activateWindow()

    def refresh(self,remote=False):
        if self.worker and self.worker.isRunning():return
        if self.fixture is not None:self.accept(self.fixture);return
        if remote and time.monotonic()-self.last_remote<10:return
        if remote:self.last_remote=time.monotonic();self.status.setText('서버 사용량 조회 중…')
        self.refresh_button.setEnabled(False)
        if self.worker:self.worker.deleteLater()
        self.worker=Fetch(self.snapshot,remote,self);self.worker.ready.connect(self.accept);self.worker.start()

    def accept(self,snapshot):
        self.refresh_button.setEnabled(True)
        if snapshot is None:self.status.setText('조회 실패 · 마지막 값 유지');return
        self.snapshot=snapshot
        for provider in snapshot['providers']:
            name=provider['id'];card,amount,status=self.cards[name]
            lines=[]
            for w in provider['windows']:
                value='—' if w['remaining'] is None else f"{w['remaining']:g}%"
                lines.append(w['label']+' '+value)
            amount.setText('\n'.join(lines) or '—');amount.setStyleSheet('font-weight:600;font-size:15px;color:'+COLORS[name])
            windows=provider['windows'];stamp=max((w['observedAt'] for w in windows),default=0)
            stale=any(w['stale'] for w in windows)
            live=sum(s['status'] in ('working','open') for s in provider['sessions'])
            status.setText(f'{age(stamp)}'+(' · 갱신 필요' if stale else '')+f'\n실행 {live} · 기록 {len(provider["sessions"])}')
            card.setToolTip(provider.get('message',''))
            if stale:amount.setStyleSheet('font-size:15px;color:#939eac')
            if provider.get('remoteState') in ('error','login'):status.setText(provider.get('remoteMessage','조회 실패'))
        for name,label,value in self.history.observe(snapshot,self.settings.get('notifications',False)):
            self.tray.showMessage('AI Glass',f'{NAMES[name]} {label} 잔여 {value:g}%')
        self.status.setText('방금 기록 확인 · ↻ 서버 사용량 조회')
        self.render_sessions()

    def select(self,name):
        self.selected=None if self.selected==name else name
        for key,(card,_,_) in self.cards.items():card.setChecked(key==self.selected)
        self.detail.setVisible(self.selected is not None);self.render_sessions();self.adjustSize();self.clamp()

    def render_sessions(self,*_):
        if not self.selected or not self.snapshot:return
        provider=next(p for p in self.snapshot['providers'] if p['id']==self.selected)
        quota=[]
        for w in provider['windows']:
            reset=w.get('resetsAt');remaining='—' if w['remaining'] is None else f"{w['remaining']:g}%"
            quota.append(f"{w['label']} {remaining} · {age(w['observedAt'])}"+(f" · {max(0,int((reset-time.time())/60))}분 후 초기화" if reset else ''))
        if self.selected=='antigravity':quota.insert(0,'Antigravity 내 모델 한도 · 별도 Claude/ChatGPT 구독과 무관')
        self.quota.setText('\n'.join(quota) or provider.get('message','수신 없음'))
        current=self.sessions.currentItem();selected_id=current.data(Qt.UserRole)['id'] if current else None
        scroll=self.sessions.verticalScrollBar().value();self.sessions.clear();favorite=self.settings.get('favorites',[]);query=self.search.text().casefold()
        for session in provider['sessions']:
            if not self.recent.isChecked() and session['status'] not in ('working','open'):continue
            key=self.selected+':'+session['id'];star=key in favorite
            if self.favorites.isChecked() and not star:continue
            if query not in (session['title']+' '+session.get('cwd','')+' '+session['id']).casefold():continue
            item=QListWidgetItem(('★ ' if star else '')+session['title']+'\n'+session.get('cwd','')+' · '+age(session['updatedAt']))
            item.setData(Qt.UserRole,session);self.sessions.addItem(item)
            if session['id']==selected_id:self.sessions.setCurrentItem(item)
        self.sessions.verticalScrollBar().setValue(scroll)
        if self.sessions.count()==0:
            empty=QListWidgetItem('해당 세션이 없습니다. 최근 기록 포함을 확인하세요.');empty.setFlags(Qt.NoItemFlags);self.sessions.addItem(empty)

    def open_session(self,item):
        session=item.data(Qt.UserRole)
        if not session:return
        def open_it():
            if session.get('pids'):
                if not desktop.focus_session(session):raise ValueError('실행 터미널을 특정할 수 없습니다. 해당 터미널의 탭을 직접 선택하세요.')
            else:desktop.open_terminal(self.selected,session)
        self.safe(open_it)

    def favorite(self):
        item=self.sessions.currentItem();session=item.data(Qt.UserRole) if item else None
        if not session:return
        key=self.selected+':'+session['id'];favorites=self.settings.setdefault('favorites',[])
        if key in favorites:favorites.remove(key)
        else:favorites.append(key)
        self.safe(self.save);self.render_sessions()

    def preferences(self):
        dialog=QDialog(self);dialog.setWindowTitle('AI Glass 설정');form=QFormLayout(dialog)
        slider=QSlider(Qt.Horizontal);slider.setRange(30,95);slider.setValue(self.settings.get('opacity',75))
        slider.valueChanged.connect(lambda value:(self.settings.update(opacity=value),self.update()));form.addRow('유리 농도',slider)
        for key,title in [('locked','위치 잠금'),('snap','가장자리 붙이기'),('notifications','잔여량 20% / 10% 알림')]:
            checkbox=QCheckBox();checkbox.setChecked(self.settings.get(key,key=='snap'))
            checkbox.toggled.connect(lambda value,k=key:self.settings.update({k:value}));form.addRow(title,checkbox)
        if WINDOWS:
            startup=QCheckBox();startup.setChecked(desktop.autostart_enabled());form.addRow('Windows 로그인 시 시작',startup)
            startup.toggled.connect(lambda value:self.safe(lambda:desktop.set_autostart(value)))
        note=QLabel('트레이 아이콘에서 위젯을 다시 열 수 있습니다.\nCLI는 Windows에 직접 설치하고 먼저 로그인하세요.');form.addRow(note)
        done=QPushButton('닫기');done.clicked.connect(dialog.accept);form.addRow(done);dialog.exec();self.safe(self.save)

    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            self.status.setText('조회 완료 후 종료할 수 있습니다. − 버튼으로 숨길 수 있습니다.');event.ignore();return
        self.safe(self.save);self.tray.hide();event.accept();QApplication.quit()


def main():
    DATA.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=DATA/'widget.log',level=logging.WARNING)
    app=QApplication(sys.argv);app.setApplicationName('AI Glass');app.setQuitOnLastWindowClosed(False)
    if '--smoke-test' in sys.argv:
        window=Glass(fixture={'providers':[{'id':n,'windows':[],'sessions':[]} for n in NAMES],'checkedAt':time.time()})
        window.show();app.processEvents()
        assert window.isVisible() and len(window.cards)==3
        (DATA/'smoke-ok').write_text('ok',encoding='utf-8')
        window.close();return 0
    lock=QLockFile(str(DATA/'windows.lock'))
    if not lock.tryLock(100):
        QMessageBox.information(None,'AI Glass','이미 실행 중입니다. 트레이 아이콘을 눌러 표시하세요.');return 0
    window=Glass();window.show();return app.exec()


if __name__=='__main__':sys.exit(main())

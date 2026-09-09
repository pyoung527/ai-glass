#!/usr/bin/env python3
"""Native GTK desktop widget; usage stays local and provider-scoped."""
import json
import logging
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

os.environ.setdefault('GDK_BACKEND', 'x11')
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango
from collector import collect, DATA, ROOT, read_json
from scripts.statusline_bridge import write_json
from usage_state import UsageState
import desktop

NAMES = {'codex':'Codex', 'claude':'Claude Code', 'antigravity':'Antigravity'}
COLORS = {'codex':'#b4dbc8', 'claude':'#e4ad94', 'antigravity':'#a4baff'}
LIVE = ('working', 'open')
WIDTH = 430


def age(stamp):
    if not stamp:
        return '수신 없음'
    minutes = max(0, int((time.time()-stamp)/60))
    return '방금' if minutes < 1 else f'{minutes}분 전' if minutes < 60 else f'{minutes//60}시간 전' if minutes < 1440 else f'{minutes//1440}일 전'


def reset_text(stamp):
    if not stamp:
        return '초기화 시각 미제공'
    seconds = stamp-time.time()
    if seconds <= 0:
        return '초기화 시각 지남 · 재수신 필요'
    minutes = max(1, math.ceil(seconds/60))
    duration = f'{minutes//1440}일 {(minutes%1440)//60}시간' if minutes >= 1440 else f'{minutes//60}시간 {minutes%60}분' if minutes >= 60 else f'{minutes}분'
    return duration+' 후 초기화'


def label(text='', css=None):
    item = Gtk.Label(label=text)
    item.set_xalign(0)
    if css:
        item.get_style_context().add_class(css)
    return item


def row(spacing=8):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)


def button(text, callback):
    item = Gtk.Button(label=text)
    item.connect('clicked', lambda _: callback())
    return item


def icon_button(icon, tooltip, callback):
    item = button('', callback)
    item.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
    item.set_always_show_image(True)
    item.set_tooltip_text(tooltip)
    item.get_accessible().set_name(tooltip)
    item.get_style_context().add_class('icon-button')
    return item


def active(widget, enabled):
    getattr(widget.get_style_context(), 'add_class' if enabled else 'remove_class')('selected')


class Widget(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='AI Glass')
        self.set_wmclass('ai-glass', 'AI Glass')
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.stick()
        self.set_icon_from_file(str(ROOT/'assets/widget.png'))
        try:
            self.settings = read_json(DATA/'settings.json', {})
        except (OSError, ValueError):
            self.settings = {}
        if not isinstance(self.settings.get('opacity', 78), (int, float)):
            self.settings['opacity'] = 78
        if not isinstance(self.settings.get('favorites', []), list):
            self.settings['favorites'] = []
        self.set_keep_above(self.settings.get('pinned', True))
        self.snapshot = None
        self.selected = None
        self.filter_all = False
        self.only_favorites = False
        self.busy = False
        self.editing = False
        self.dragging = False
        self.search_text = ''
        self.toast_until = 0
        self.session_rows = {}
        self.diagnostics = {}
        self.usage_state = UsageState(DATA/'usage-history.json')
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.style()
        self.glass_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.glass_box.set_name('glass')
        self.add(self.glass_box)
        header = row(5)
        header.set_border_width(8)
        title = Gtk.EventBox()
        brand = row(7)
        brand.pack_start(Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(ROOT/'assets/widget.png'),24,24,True)),False,False,0)
        brand.pack_start(label('AI Glass','brand'),False,False,0)
        title.add(brand)
        title.connect('button-press-event', self.drag)
        header.pack_start(title,True,True,0)
        self.pin = icon_button('view-pin-symbolic','항상 위에 표시',self.toggle_pin)
        self.update_pin()
        header.pack_start(self.pin,False,False,0)
        self.settings_button = icon_button('preferences-system-symbolic','설정',self.preferences)
        header.pack_start(self.settings_button,False,False,0)
        header.pack_start(icon_button('window-minimize-symbolic','숨기기 · 앱 실행으로 복귀',self.hide),False,False,0)
        header.pack_start(icon_button('window-close-symbolic','AI Glass 종료',self.close),False,False,0)
        self.glass_box.pack_start(header,False,False,0)
        overview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
        overview.set_border_width(8)
        overview.pack_start(label('남은 사용량','muted'),False,False,0)
        grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=3,homogeneous=True)
        self.tiles = {}
        for name in NAMES:
            tile = button('',lambda n=name:self.select(n))
            tile.remove(tile.get_child())
            tile.get_style_context().add_class('provider')
            tile.get_accessible().set_name(NAMES[name]+' 세션 열기')
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
            overlay = Gtk.Overlay()
            drawing = Gtk.Image()
            self.draw_ring(drawing,name)
            overlay.add(drawing)
            path = ROOT/'assets'/('antigravity.png' if name=='antigravity' else name+'.svg')
            icon = Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path),23,23,True))
            icon.set_halign(Gtk.Align.CENTER)
            icon.set_valign(Gtk.Align.CENTER)
            overlay.add_overlay(icon)
            box.pack_start(overlay,False,False,0)
            for text,css in [(NAMES[name],'provider-name'),('—','amount'),('수신 확인 중','muted'),('','muted')]:
                item = label(text,css)
                item.set_xalign(.5)
                item.set_justify(Gtk.Justification.CENTER)
                box.pack_start(item,False,False,0)
            tile.add(box)
            grid.pack_start(tile,True,True,0)
            self.tiles[name] = (tile,drawing,*box.get_children()[2:])
            if name=='antigravity':
                subtitle=label('모델별 주간 잔여량','muted')
                subtitle.set_xalign(.5)
                box.pack_start(subtitle,False,False,0)
                box.reorder_child(subtitle,2)
        overview.pack_start(grid,False,False,0)
        self.glass_box.pack_start(overview,False,False,0)
        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=9)
        self.details.set_border_width(12)
        self.panel_scroll = Gtk.ScrolledWindow()
        self.panel_scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        self.panel_scroll.add(self.details)
        self.glass_box.pack_start(self.panel_scroll,True,True,0)
        footer = row(5)
        footer.set_border_width(8)
        self.updated = label('로컬 기록 확인 중','muted')
        self.updated.set_ellipsize(Pango.EllipsizeMode.END)
        self.updated.set_max_width_chars(46)
        footer.pack_start(self.updated,True,True,0)
        self.refresh_button = icon_button('view-refresh-symbolic','최신 사용량 조회',lambda:self.refresh(remote=True))
        footer.pack_start(self.refresh_button,False,False,0)
        self.glass_box.pack_end(footer,False,False,0)
        self.connect('delete-event',self.save_position)
        self.connect('key-press-event',self.keypress)
        self.connect('configure-event',self.configured)
        self.get_screen().connect('monitors-changed',lambda *_:self.fit_window())
        self.show_all()
        self.panel_scroll.hide()
        self.resize(WIDTH,1)
        monitor = self.get_display().get_primary_monitor() or self.get_display().get_monitor(0)
        area = monitor.get_workarea()
        position = self.settings.get('position', {})
        if not isinstance(position, dict):
            position = {}
        x,y = position.get('x',area.x+area.width-WIDTH-20), position.get('y',area.y+30)
        self.move(int(x) if isinstance(x,(int,float)) else area.x,int(y) if isinstance(y,(int,float)) else area.y)
        GLib.idle_add(self.fit_window)
        self.refresh()
        GLib.timeout_add_seconds(15,self.refresh)

    def style(self):
        opacity = min(95,max(45,self.settings.get('opacity',78)))/100
        size = 15 if self.settings.get('compact',True) else 19
        self.css.load_from_data(('''
        window { background-color:transparent; }
        #glass { background-image:linear-gradient(135deg,rgba(255,255,255,.06),rgba(255,255,255,0)); background-color:rgba(24,30,40,ALPHA); border:1px solid rgba(255,255,255,.25); border-radius:20px; color:#f0f2f5; }
        label { color:#f0f2f5; font-family:"Noto Sans"; font-size:11px; }
        .brand { font-weight:bold; font-size:13px; }
        .muted { color:#bbc3cf; font-size:10px; }
        .amount { font-size:SIZEpx; font-weight:bold; }
        .stale-value { color:#939eac; }
        .warning { color:#edc58b; font-weight:bold; }
        .provider-name { font-weight:bold; font-size:11px; }
        button { color:#d7e0eb; background-image:none; background-color:transparent; border:0; box-shadow:none; text-shadow:none; border-radius:8px; padding:5px; }
        button:hover { background-color:rgba(255,255,255,.09); }
        button:focus { outline:2px solid #afc9e3; outline-offset:-2px; }
        .provider { padding:4px 3px; }
        .selected { background-color:rgba(156,191,232,.18); }
        .icon-button { min-width:21px; min-height:21px; padding:4px; }
        .error label { color:#ffb7a8; }
        entry { color:#eee; background:#2c3442; border:1px solid #596274; border-radius:7px; }
        .session { border-bottom:1px solid rgba(255,255,255,.08); padding:6px 0; }
        scrollbar { background:transparent; } scrollbar slider { background:#566070; min-width:4px; border-radius:3px; }
        scale trough { background:#4b5668; } scale highlight { background:#c9d8e8; }
        ''').replace('ALPHA',str(opacity)).replace('SIZE',str(size)).encode())

    def tell(self,text):
        self.toast_until = time.monotonic()+8
        self.updated.set_text(text)
        self.updated.set_tooltip_text(text)

    def safe(self,callback):
        try:
            callback()
            return True
        except Exception as error:
            logging.exception('Desktop action failed')
            self.tell(str(error))
            return False

    def drag(self,_,event):
        if event.button==1 and not self.settings.get('locked',False):
            self.dragging=True
            self.begin_move_drag(event.button,int(event.x_root),int(event.y_root),event.time)
        return True

    def configured(self,*_):
        if getattr(self,'position_timer',0):
            GLib.source_remove(self.position_timer)
        self.position_timer=GLib.timeout_add(350,self.finish_move)
        return False

    def finish_move(self):
        self.position_timer=0
        if self.dragging:
            self.dragging=False
            self.fit_window(snap=True)
            self.save_position()
        return False

    def fit_window(self,snap=False):
        if not self.get_window():
            return False
        area=self.get_display().get_monitor_at_window(self.get_window()).get_workarea()
        width,height=self.get_size()
        x,y=self.get_position()
        right,bottom=area.x+area.width-width,area.y+area.height-height
        nx,ny=max(area.x,min(x,right)),max(area.y,min(y,bottom))
        if snap and self.settings.get('snap',True):
            if abs(nx-area.x)<24: nx=area.x
            elif abs(nx-right)<24: nx=right
            if abs(ny-area.y)<24: ny=area.y
            elif abs(ny-bottom)<24: ny=bottom
        if (nx,ny)!=(x,y): self.move(nx,ny)
        return False

    def keypress(self,_,event):
        if event.keyval==Gdk.KEY_Escape: self.select(None)

    def save_position(self,*_):
        x,y=self.get_position()
        self.settings['position']={'x':x,'y':y}
        try: write_json(DATA/'settings.json',self.settings)
        except OSError: self.tell('설정을 저장하지 못했습니다. 폴더 권한을 확인하세요.')
        return False

    def update_pin(self):
        enabled=self.settings.get('pinned',True)
        active(self.pin,enabled)
        self.pin.set_tooltip_text('항상 위 표시: '+('켜짐' if enabled else '꺼짐'))

    def toggle_pin(self):
        self.settings['pinned']=not self.settings.get('pinned',True)
        self.set_keep_above(self.settings['pinned'])
        self.update_pin()
        self.save_position()

    def provider(self,name):
        return next((p for p in (self.snapshot or {}).get('providers',[]) if p['id']==name),None)

    def draw_ring(self,drawing,name):
        size=36 if self.settings.get('compact',True) else 64
        circumference=2*math.pi*30
        arc=circumference*.75
        circles=f'<circle cx="35" cy="35" r="30" fill="none" stroke="#ffffff" stroke-opacity=".12" stroke-width="3" stroke-dasharray="{arc} {circumference}" transform="rotate(135 35 35)"/>'
        provider=self.provider(name)
        values=[w['remaining'] for w in provider['windows'] if w['remaining'] is not None and not w['stale']] if provider else []
        if values:
            circles+=f'<circle cx="35" cy="35" r="30" fill="none" stroke="{COLORS[name]}" stroke-width="3" stroke-dasharray="{arc*min(values)/100} {circumference}" transform="rotate(135 35 35)"/>'
        loader=GdkPixbuf.PixbufLoader.new_with_type('svg')
        loader.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 70 70">{circles}</svg>'.encode())
        loader.close()
        drawing.set_from_pixbuf(loader.get_pixbuf())

    def refresh(self,remote=False):
        if self.busy: return True
        if remote and time.monotonic()-getattr(self,'last_remote',0)<10:
            self.tell('연속 조회는 10초 후 가능합니다')
            return True
        if remote:
            self.last_remote=time.monotonic()
            self.diagnostics.clear()
            for tile in self.tiles.values(): tile[3].set_text('최신 사용량 조회 중…')
            self.tell('서버에서 최신 사용량을 조회합니다')
        self.busy=True
        self.refresh_button.set_sensitive(False)
        previous=self.snapshot
        def run():
            try:
                if remote:
                    import remote_usage
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        jobs={pool.submit(remote_usage.query,name):name for name in NAMES}
                        for job in as_completed(jobs):
                            name=jobs[job]
                            try: status=job.result()
                            except Exception: status={'state':'error','message':'조회 결과를 저장하지 못했습니다'}
                            GLib.idle_add(self.remote_progress,name,status)
                result=collect(previous)
            except Exception:
                logging.exception('Collection failed')
                result=None
            GLib.idle_add(self.accept,result)
        threading.Thread(target=run,daemon=True).start()
        return True

    def remote_progress(self,name,status):
        self.tiles[name][3].set_text(status['message'])
        self.tell(NAMES[name]+': '+status['message'])
        return False

    def accept(self,result):
        self.busy=False
        self.refresh_button.set_sensitive(True)
        if result is None:
            self.tell('수집 오류 · 마지막 값 유지 · 로그를 확인하세요')
            return False
        self.snapshot=result
        for name,(tile,drawing,amount,period,sessions) in self.tiles.items():
            p=self.provider(name)
            lines=[]
            tips=[]
            display_windows=sorted(p['windows'],key=lambda w:w.get('key')!='gemini-weekly') if name=='antigravity' else p['windows']
            for w in display_windows:
                short={'Gemini 주간':'Gemini','Claude/GPT 주간':'Claude·GPT'}.get(w['label'],w['label'])
                remaining='—' if w['remaining'] is None else f"{w['remaining']:g}%"
                lines.append(f'{short} {remaining}')
                tips.append(f"{w['label']}: {remaining}\n{reset_text(w['resetsAt'])}\n{age(w['observedAt'])} 수신 · {w['source']}")
            amount.set_text('\n'.join(lines) or '—')
            stamps=[w['observedAt'] for w in p['windows']]
            stale=any(w['stale'] for w in p['windows'])
            period.set_text('읽기 오류' if p.get('error') else p['connectionLabel'] if p.get('connectionLabel') in ('로그인 필요','연결 설정 필요') else (age(min(stamps))+(' · 이전 값' if stale else ' 수신')) if stamps else p.get('connectionLabel','수신 없음'))
            if name=='antigravity':
                getattr(amount.get_style_context(),'add_class' if stale else 'remove_class')('stale-value')
                getattr(period.get_style_context(),'add_class' if stale else 'remove_class')('warning')
                if stale and stamps and not p.get('error'):
                    period.set_text('⚠ '+age(min(stamps))+' 기록\n갱신 필요')
                tips.insert(0,'Antigravity에서 제공하는 모델별 주간 한도입니다.\n별도 ChatGPT·Claude 구독 한도와 무관합니다.')
            if p.get('remoteState') in ('login','error'):
                period.set_text('로그인 필요' if p['remoteState']=='login' else '서버 조회 실패\n이전 값 유지')
                tips.insert(0,p['remoteMessage'])
            getattr(tile.get_style_context(),'add_class' if p.get('error') else 'remove_class')('error')
            tile.set_tooltip_text('\n\n'.join(tips) if tips else p['message'])
            count=sum(s['status'] in LIVE for s in p['sessions'])
            sessions.set_text(f'실행 {count} · 기록 {len(p["sessions"])}')
            self.draw_ring(drawing,name)
        if time.monotonic() >= self.toast_until:
            self.updated.set_text('로컬 확인 '+time.strftime('%H:%M:%S')+' · 수신 시각은 제공자별 표시')
        for name,period,remaining in self.usage_state.observe(result,self.settings.get('notifications',False)):
            notification=Gio.Notification.new(f'{NAMES[name]} 사용량 부족')
            notification.set_body(f'{period} · {remaining:g}% 남음')
            notification.set_default_action('app.show')
            self.get_application().send_notification(name+'-quota',notification)
        if self.selected and not self.editing:
            self.update_details()
        return False

    def clear_details(self):
        for child in self.details.get_children(): self.details.remove(child)
        self.session_rows={}

    def show_panel(self):
        self.panel_scroll.set_size_request(-1,330)
        if self.get_window():
            area=self.get_display().get_monitor_at_window(self.get_window()).get_workarea()
            base=self.glass_box.get_preferred_height()[0]-self.panel_scroll.get_preferred_height()[0]
            self.panel_scroll.set_size_request(-1,max(120,min(380,area.height-base-20)))
        self.panel_scroll.show_all()
        self.resize(WIDTH,1)
        GLib.idle_add(self.fit_window)

    def select(self,name):
        self.selected=None if self.selected==name else name
        self.editing=False
        for key,(tile,*_) in self.tiles.items(): active(tile,key==self.selected)
        self.clear_details()
        self.panel_scroll.hide()
        if self.selected and self.snapshot:
            self.render_details()
        else:
            self.resize(WIDTH,1)

    def render_details(self):
        self.clear_details()
        self.details.pack_start(label(NAMES[self.selected]+' 세션','brand'),False,False,0)
        if self.selected=='antigravity':
            explanation=label('Antigravity에서 제공하는 모델별 주간 한도입니다.\n별도 ChatGPT·Claude 구독 한도와 무관합니다.\n하단 갱신 버튼으로 최신 한도를 조회하세요. CLI의 /usage로도 확인할 수 있습니다.','muted')
            explanation.set_line_wrap(True)
            explanation.set_max_width_chars(44)
            self.details.pack_start(explanation,False,False,0)
            self.details.pack_start(button('Antigravity CLI 열기',self.open_antigravity),False,False,0)
        tabs=row(4)
        self.live_tab=button('실행 중',lambda:self.set_filter(False))
        self.recent_tab=button('최근 기록',lambda:self.set_filter(True))
        self.favorite_tab=button('★ 즐겨찾기',self.toggle_favorite_filter)
        for child in (self.live_tab,self.recent_tab,self.favorite_tab): tabs.pack_start(child,False,False,0)
        self.details.pack_start(tabs,False,False,0)
        self.search=Gtk.SearchEntry()
        self.search.set_placeholder_text('프로젝트 · 제목 · 세션 ID 검색')
        self.search.set_text(self.search_text)
        self.search.connect('search-changed',self.search_changed)
        self.details.pack_start(self.search,False,False,0)
        self.quota_box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4)
        self.quota_labels={}
        self.details.pack_start(self.quota_box,False,False,0)
        self.listing=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.details.pack_start(self.listing,False,False,0)
        self.empty=label('','muted')
        self.empty.set_no_show_all(True)
        self.details.pack_start(self.empty,False,False,0)
        history=Gtk.Expander(label='최근 7일 사용량 추이')
        self.chart=Gtk.DrawingArea()
        self.chart.set_size_request(-1,125)
        self.chart.connect('draw',self.draw_history)
        history.add(self.chart)
        self.details.pack_start(history,False,False,0)
        actions=row(3)
        actions.pack_start(button('사용량 안내' if self.selected=='antigravity' else '사용량 확인',self.open_usage),False,False,0)
        actions.pack_start(button('연결 점검',self.diagnose),False,False,0)
        actions.pack_start(button('확인 명령 복사',self.copy_usage),False,False,0)
        self.details.pack_start(actions,False,False,0)
        self.note=label('','muted')
        self.note.set_line_wrap(True)
        self.note.set_max_width_chars(45)
        self.details.pack_start(self.note,False,False,0)
        self.connection_action=button('연결 복구',self.repair_connection)
        self.connection_action.set_no_show_all(True)
        self.details.pack_start(self.connection_action,False,False,0)
        self.update_details()
        self.show_panel()
        self.update_details()

    def set_filter(self,all_sessions):
        self.filter_all=all_sessions
        self.update_sessions()

    def toggle_filter(self):
        self.set_filter(not self.filter_all)

    def toggle_favorite_filter(self):
        self.only_favorites=not self.only_favorites
        self.update_sessions()

    def search_changed(self,entry):
        self.search_text=entry.get_text()
        self.update_sessions()

    def favorite_key(self,session):
        return self.selected+':'+session['id']

    def favorite(self,session):
        key=self.favorite_key(session)
        favorites=self.settings.setdefault('favorites',[])
        if key in favorites: favorites.remove(key)
        else: favorites.append(key)
        self.save_position()
        self.update_sessions()

    def update_sessions(self):
        active(self.live_tab,not self.filter_all)
        active(self.recent_tab,self.filter_all)
        active(self.favorite_tab,self.only_favorites)
        favorites=self.settings.get('favorites',[])
        query=self.search_text.casefold().strip()
        sessions=[s for s in self.provider(self.selected)['sessions'] if (self.filter_all or s['status'] in LIVE) and (not self.only_favorites or self.favorite_key(s) in favorites) and (not query or query in ' '.join([s.get('cwd',''),s['title'],s['id']]).casefold())]
        sessions.sort(key=lambda s:(self.favorite_key(s) not in favorites,-s['updatedAt']))
        ids={s['id'] for s in sessions}
        for sid in list(self.session_rows):
            if sid not in ids:
                self.listing.remove(self.session_rows.pop(sid)['row'])
        for index,s in enumerate(sessions):
            sid=s['id']
            if sid not in self.session_rows:
                line=row(3)
                line.get_style_context().add_class('session')
                info=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
                project=label()
                title=label('', 'muted')
                for text in (project,title):
                    text.set_single_line_mode(True)
                    text.set_ellipsize(Pango.EllipsizeMode.END)
                    text.set_max_width_chars(27)
                    info.pack_start(text,False,False,0)
                item={'row':line,'project':project,'title':title,'session':s}
                open_button=button('',lambda i=item:self.open_session(i['session']))
                open_button.remove(open_button.get_child())
                open_button.add(info)
                line.pack_start(open_button,True,True,0)
                item['open']=open_button
                star=icon_button('non-starred-symbolic','즐겨찾기',lambda i=item:self.favorite(i['session']))
                launch=icon_button('go-jump-symbolic','세션으로 이동',lambda i=item:self.open_session(i['session']))
                copy=icon_button('edit-copy-symbolic','재개 명령 복사',lambda i=item:self.copy_session(i['session']))
                for action in (star,launch,copy): line.pack_start(action,False,False,0)
                item.update(star=star,launch=launch)
                self.session_rows[sid]=item
                self.listing.pack_start(line,False,False,0)
            item=self.session_rows[sid]
            item['session']=s
            state={'working':'작업 중','open':'실행 중','recent':'최근 활동','saved':'저장됨'}[s['status']]
            project=Path(s['cwd']).name if s.get('cwd') else '프로젝트 미확인'
            item['project'].set_text(f'{project} · {state}')
            item['title'].set_text(' '.join(s['title'].split()))
            item['row'].set_tooltip_text(f"{s.get('cwd') or '경로 미확인'}\n{' '.join(s['title'].split())[:240]}\n{age(s['updatedAt'])} · {sid}")
            active(item['star'],self.favorite_key(s) in favorites)
            item['star'].set_tooltip_text('즐겨찾기 해제' if self.favorite_key(s) in favorites else '즐겨찾기 추가')
            action_text='실행 창으로 이동' if s['status'] in LIVE else '터미널에서 세션 재개'
            item['launch'].set_tooltip_text(action_text)
            item['open'].set_tooltip_text(action_text)
            item['open'].get_accessible().set_name(project+' · '+s['title']+' · '+action_text)
            self.listing.reorder_child(item['row'],index)
        self.empty.set_text('검색 결과가 없습니다.' if query else '즐겨찾기가 없습니다.' if self.only_favorites else '확인된 실행 세션이 없습니다. 최근 기록 탭을 확인하세요.' if not self.filter_all else '저장된 세션이 없습니다.')
        self.empty.set_line_wrap(True)
        self.empty.set_max_width_chars(40)
        self.empty.set_visible(not sessions)
        self.listing.show_all()

    def update_details(self):
        p=self.provider(self.selected)
        keys=set()
        for w in p['windows']:
            key=w.get('key',w['label'])
            keys.add(key)
            if key not in self.quota_labels:
                item=label('','muted')
                item.set_line_wrap(True)
                item.set_max_width_chars(44)
                self.quota_labels[key]=item
                self.quota_box.pack_start(item,False,False,0)
            remaining='확인 필요' if w['remaining'] is None else f"{w['remaining']:g}% 남음"
            self.quota_labels[key].set_text(f"{w['label']} · {remaining} · {reset_text(w['resetsAt'])}\n{age(w['observedAt'])} 수신"+(' · 이전 기록' if w['stale'] else ''))
            if self.selected=='antigravity':
                self.quota_labels[key].set_text(self.quota_labels[key].get_text()+'\n마지막 수신: '+time.strftime('%m/%d %H:%M:%S',time.localtime(w['observedAt'])))
        for key in list(self.quota_labels):
            if key not in keys: self.quota_box.remove(self.quota_labels.pop(key))
        self.quota_box.show_all()
        self.update_sessions()
        self.note.set_text(self.diagnostics.get(self.selected,p['message']))
        self.connection_action.set_label('Claude 구독 로그인' if p.get('action')=='login' else '상태 표시줄 연결 복구')
        self.connection_action.set_visible(p.get('action')=='login' or (not p['windows'] and self.selected!='codex'))
        self.chart.queue_draw()

    def draw_history(self,widget,cr):
        width,height=widget.get_allocated_width(),widget.get_allocated_height()
        cr.set_font_size(10)
        now=time.time()
        cr.set_source_rgb(.66,.7,.76)
        cr.move_to(2,12); cr.show_text('100%')
        cr.move_to(2,height-20); cr.show_text('0%')
        cr.move_to(35,height-4); cr.show_text('7일 전')
        cr.move_to(width-32,height-4); cr.show_text('현재')
        palette=[(.55,.8,.95),(.9,.68,.55),(.7,.65,.95),(.65,.85,.67)]
        any_points=False
        for index,window in enumerate(self.provider(self.selected)['windows']):
            points=self.usage_state.series(self.selected,window,now)
            cr.set_source_rgb(*palette[index%len(palette)])
            cr.move_to(45+(index%2)*150,12+(index//2)*13); cr.show_text(window['label'])
            previous=None
            for point in points:
                any_points=True
                x=35+(point['time']-(now-7*86400))/(7*86400)*(width-42)
                y=36+(100-point['remaining'])/100*(height-58)
                if previous and point['time']-previous['time'] <= 900 and point.get('reset')==previous.get('reset'):
                    cr.move_to(px,py); cr.line_to(x,y); cr.set_line_width(1.5); cr.stroke()
                cr.arc(x,y,2,0,2*math.pi); cr.fill()
                previous,px,py=point,x,y
        cr.set_source_rgb(.72,.76,.81)
        cr.move_to(42,height/2)
        if not any_points: cr.show_text('새 사용량 수신 후 기록을 시작합니다')
        widget.set_tooltip_text('최근 7일 · 실제 수신값만 표시\n15분 이상 수신 공백과 초기화 전후는 선으로 연결하지 않습니다.\n최대 30일 / 한도별 2,000개 관측값을 로컬에 보관합니다.')
        return False

    def copy_session(self,s):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(desktop.resume_command(self.selected,s),-1)
        self.tell('세션 재개 명령을 복사했습니다')

    def open_session(self,s):
        if s['status'] in LIVE:
            self.safe(lambda:self.focus_session(s))
        elif self.safe(lambda:desktop.resume(self.selected,s)):
            self.tell('터미널에서 세션을 재개합니다')

    def focus_session(self,session):
        matches=desktop.session_windows(session)
        if not matches:
            self.tell('실행 창을 찾지 못했습니다. 앱에서 세션을 선택하거나 재개 명령을 복사하세요.')
            return
        if len(matches)==1:
            desktop.focus_window(matches[0],Gtk.get_current_event_time())
            return
        dialog=Gtk.Dialog(title='이동할 창 선택',transient_for=self,modal=True)
        dialog.add_button('취소',Gtk.ResponseType.CANCEL)
        box=dialog.get_content_area()
        box.set_border_width(12)
        box.pack_start(label('같은 앱의 창이 여러 개입니다. 이동할 창을 선택하세요.'),False,False,8)
        for window in matches:
            def choose(target=window):
                desktop.focus_window(target,Gtk.get_current_event_time())
                dialog.destroy()
            box.pack_start(button(window.get_name()[:100],choose),False,False,0)
        dialog.connect('response',lambda *_:dialog.destroy())
        dialog.show_all()

    def copy_usage(self):
        command={'codex':'codex → /status','claude':'claude → /usage','antigravity':'agy → /usage'}[self.selected]
        # Copy a runnable CLI command; the slash command is entered in that CLI.
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(command.split(' → ')[0],-1)
        self.tell(command+' 순서로 확인하세요. 실행 명령을 복사했습니다.')

    def open_antigravity(self):
        import shutil
        executable=shutil.which('agy')
        if not executable:
            self.tell('Antigravity CLI(agy)를 찾지 못했습니다. 설치 경로를 확인하세요.')
            return
        if self.safe(lambda:subprocess.Popen(['x-terminal-emulator','--',executable],start_new_session=True)):
            self.tell('Antigravity CLI에서 /usage를 입력하세요')

    def open_usage(self):
        url={'codex':'https://chatgpt.com/codex/settings/usage','claude':'https://claude.ai/settings/usage','antigravity':'https://www.antigravity.google/docs/cli/commands/usage'}[self.selected]
        self.safe(lambda:Gio.AppInfo.launch_default_for_uri(url,None))

    def diagnose(self):
        name=self.selected
        self.tell('연결·로그인·마지막 수신을 확인 중입니다')
        def run():
            from collector import connection_status, HOME
            home={'codex':Path(os.getenv('CODEX_HOME',HOME/'.codex')),'claude':Path(os.getenv('CLAUDE_CONFIG_DIR',HOME/'.claude')),'antigravity':Path(os.getenv('ANTIGRAVITY_CONFIG_DIR',HOME/'.gemini/antigravity-cli'))}[name]
            try:
                if name=='codex':
                    message='Codex 로컬 DB '+('확인됨' if list(home.glob('state_*.sqlite')) else '없음')+' · Codex의 /status에서 한도를 확인하세요.'
                else:
                    status=connection_status(name,home)
                    cache=read_json(DATA/f'{name}-connection.json',{})
                    message=status['message'] if not cache.get('hasQuota') or status.get('connectionLabel') in ('로그인 필요','연결 설정 필요') else '한도 전달 확인됨 · 마지막 호출 '+age(cache.get('lastCalled',0))+' · CLI에서 /usage를 확인하세요.'
                GLib.idle_add(self.diagnostic_result,name,message)
            except Exception as error:
                GLib.idle_add(self.diagnostic_result,name,'점검 실패: '+type(error).__name__)
        threading.Thread(target=run,daemon=True).start()

    def diagnostic_result(self,name,message):
        self.diagnostics[name]=message
        if self.selected==name and not self.editing: self.note.set_text(message)
        self.tell('연결 점검 완료 · 상세 안내를 확인하세요')
        return False

    def repair_connection(self):
        if self.provider(self.selected).get('action')=='login':
            self.safe(lambda:subprocess.Popen(['x-terminal-emulator','--','claude','auth','login','--claudeai'],start_new_session=True))
            return
        name=self.selected
        def run():
            args=[sys.executable,str(ROOT/'scripts/statusline_bridge.py'),'--install']
            if name=='antigravity': args.append('--antigravity')
            try:
                result=subprocess.run(args,capture_output=True,text=True,timeout=10)
                message=result.stdout.strip() if result.returncode==0 else '연결 복구 실패 · 설정 파일을 확인하세요'
            except (OSError,subprocess.TimeoutExpired): message='연결 복구 실패 · 로그를 확인하세요'
            GLib.idle_add(self.diagnostic_result,name,message)
        threading.Thread(target=run,daemon=True).start()

    def preferences(self):
        self.select(None)
        self.editing=True
        self.clear_details()
        self.details.pack_start(label('위젯 설정','brand'),False,False,0)
        self.details.pack_start(label('유리 농도','muted'),False,False,0)
        slider=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,45,95,1)
        slider.set_value(self.settings.get('opacity',78))
        slider.connect('value-changed',lambda scale:self.set_option('opacity',int(scale.get_value())))
        self.details.pack_start(slider,False,False,0)
        for key,text,default in [('compact','작은 화면 모드',True),('locked','위치 잠금',False),('snap','화면 가장자리 붙이기',True),('notifications','20% · 10% 사용량 부족 알림',False)]:
            self.add_switch(text,self.settings.get(key,default),lambda value,k=key:self.set_option(k,value))
        self.add_switch('로그인 시 자동 시작',desktop.autostart_enabled(),desktop.set_autostart)
        try:
            self.add_switch('Super + Alt + G · 표시 / 숨김',desktop.shortcut_enabled(),desktop.set_shortcut)
        except (ValueError,GLib.Error):
            self.details.pack_start(label('전역 단축키: GNOME 환경에서 지원','muted'),False,False,0)
        self.details.pack_start(button('화면 오른쪽 위로 이동',self.move_home),False,False,0)
        note=label('갱신 버튼은 서버의 최신 사용량을 조회합니다.\n알림은 15분 이내 수신값에만 적용합니다.\n숨긴 위젯은 앱 실행이나 단축키로 다시 표시합니다.','muted')
        note.set_line_wrap(True)
        note.set_max_width_chars(43)
        self.details.pack_start(note,False,False,0)
        self.details.pack_start(button('닫기',lambda:self.select(None)),False,False,0)
        self.show_panel()

    def add_switch(self,text,value,callback):
        line=row()
        caption=label(text)
        caption.set_line_wrap(True)
        caption.set_max_width_chars(34)
        line.pack_start(caption,True,True,0)
        switch=Gtk.Switch()
        switch.set_active(value)
        switch.get_accessible().set_name(text)
        def changed(item,state):
            if self.safe(lambda:callback(state)):
                item.set_state(state)
            else:
                item.set_active(item.get_state())
            return True
        switch.connect('state-set',changed)
        line.pack_end(switch,False,False,0)
        self.details.pack_start(line,False,False,0)

    def set_option(self,key,value):
        self.settings[key]=value
        self.save_position()
        if key in ('opacity','compact'):
            self.style()
            for name,(_,drawing,*_) in self.tiles.items(): self.draw_ring(drawing,name)
            self.resize(WIDTH,1)
            GLib.idle_add(self.fit_window)

    def move_home(self):
        area=self.get_display().get_monitor_at_window(self.get_window()).get_workarea()
        self.move(area.x+area.width-self.get_size().width-12,area.y+12)
        self.save_position()


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='local.ai.glass',flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window=None
        self.smoke_mode=False
        action=Gio.SimpleAction.new('show',None)
        action.connect('activate',lambda *_:self.do_activate())
        self.add_action(action)

    def do_command_line(self,command):
        args=command.get_arguments()
        self.smoke_mode='--smoke-test' in args
        if '--toggle' in args and self.window and self.window.get_visible():
            self.window.hide()
        else: self.do_activate()
        return 0

    def do_activate(self):
        if self.window is None:
            self.window=Widget(self)
            signal.signal(signal.SIGTERM,lambda *_:GLib.idle_add(self.window.close))
        self.window.show()
        self.window.deiconify()
        self.window.present()
        if self.smoke_mode: GLib.timeout_add_seconds(3,self.smoke)

    def smoke(self):
        try:
            w=self.window
            assert w.snapshot and len(w.tiles)==3
            assert not w.get_decorated()
            self.collapsed_height=w.get_size().height
            w.tiles['codex'][0].clicked()
            GLib.timeout_add(400,self.smoke_panel,'sessions')
        except Exception:
            logging.exception('UI smoke failed'); os._exit(1)
        return False

    def smoke_panel(self,stage):
        try:
            w=self.window
            if stage in ('sessions','settings'):
                assert w.details.get_mapped()
                assert w.get_size().height>self.collapsed_height+50
                if stage=='sessions':
                    assert w.details.get_children()[0].get_text()=='Codex 세션'
                    w.set_filter(True)
                    before={sid:item['row'] for sid,item in w.session_rows.items()}
                    w.accept(w.snapshot)
                    assert all(w.session_rows[sid]['row'] is item for sid,item in before.items()), 'Refresh must preserve rows'
                    w.search.set_text('nonexistent-session-search-938429')
                    w.search_changed(w.search)
                    assert not w.session_rows
                    w.search.set_text(''); w.search_changed(w.search)
                    assert w.session_rows or not w.provider('codex')['sessions']
                else:
                    assert any(isinstance(c,Gtk.Scale) and c.get_mapped() for c in w.details.get_children())
                capture=Gdk.pixbuf_get_from_window(w.get_window(),0,0,*w.get_size())
                (DATA/'test-results').mkdir(parents=True,exist_ok=True)
                if capture: capture.savev(str(DATA/f'test-results/native-{stage}.png'),'png',[],[])
                if stage=='sessions':
                    w.settings_button.clicked(); GLib.timeout_add(400,self.smoke_panel,'settings')
                else:
                    w.select(None); GLib.timeout_add(400,self.smoke_panel,'collapsed')
            else:
                assert not w.details.get_mapped()
                assert w.get_size().height<=self.collapsed_height
                print('PASS: panels, stable session rows, search, settings, collapse',flush=True)
                self.quit()
        except Exception:
            logging.exception('UI smoke failed'); os._exit(1)
        return False


if __name__=='__main__':
    DATA.mkdir(parents=True,exist_ok=True)
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.WARNING,handlers=[RotatingFileHandler(DATA/'widget.log',maxBytes=256*1024,backupCount=2),logging.StreamHandler()])
    Application().run(sys.argv)

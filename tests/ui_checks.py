"""Run with GSETTINGS_BACKEND=memory /usr/bin/python3 tests/ui_checks.py."""
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import native
import remote_usage
from gi.repository import Gtk, GLib, Gdk


def settle():
    deadline=time.monotonic()+.35
    while time.monotonic()<deadline:
        while Gtk.events_pending(): Gtk.main_iteration_do(False)
        time.sleep(.01)


with tempfile.TemporaryDirectory() as tmp:
    now=time.time()
    fixture={'checkedAt':now,'providers':[]}
    for name in native.NAMES:
        sessions=[{'id':str(i),'title':'작업 '+str(i),'cwd':'/tmp/project-'+str(i),'status':'open' if i<2 else 'saved','updatedAt':now-i,'pids':[]} for i in range(35)]
        fixture['providers'].append({'id':name,'message':'테스트 연결','sessions':sessions,'windows':[{'key':'weekly','label':'주간','remaining':50,'observedAt':now,'resetsAt':now+86400,'stale':False,'source':'테스트'}]})
    with patch.object(native,'DATA',Path(tmp)), patch.object(native,'collect',return_value=fixture):
        app=native.Application()
        app.set_application_id('local.ai.glass.qa')
        app.register(None)
        window=native.Widget(app)
        settle()
        with patch.object(remote_usage,'query',return_value={'state':'ok','message':'테스트 조회 완료'}) as query:
            window.refresh_button.clicked(); settle()
            assert sorted(call.args[0] for call in query.call_args_list)==sorted(native.NAMES)
            window.refresh_button.clicked(); settle()
            assert query.call_count==3, 'Repeated click must be throttled'
        collapsed=window.get_size().height
        window.tiles['codex'][0].clicked(); settle()
        assert len(window.session_rows)==2
        assert not window.empty.get_visible()
        assert window.pin.get_image().get_mapped()
        window.recent_tab.clicked(); settle()
        assert len(window.session_rows)==35
        with patch.object(window,'open_session') as opened:
            for sid in ('0','12'):
                window.session_rows[sid]['open'].clicked()
                opened.assert_called_with(window.session_rows[sid]['session'])
            opened.reset_mock()
            window.session_rows['12']['star'].clicked()
            window.session_rows['12']['star'].clicked()
            opened.assert_not_called()
        adjustment=window.panel_scroll.get_vadjustment()
        adjustment.set_value(150); settle()
        old_rows={sid:item['row'] for sid,item in window.session_rows.items()}
        window.search.grab_focus(); settle()
        adjustment.set_value(150); settle()
        before_scroll=adjustment.get_value()
        before_focus=window.get_focus()
        assert before_focus is window.search
        window.accept(fixture); settle()
        assert adjustment.get_value()==before_scroll,(before_scroll,adjustment.get_value())
        assert window.get_focus() is before_focus
        assert all(window.session_rows[sid]['row'] is row for sid,row in old_rows.items())
        window.favorite(window.session_rows['12']['session']); settle()
        assert window.settings['favorites']==['codex:12']
        window.favorite_tab.clicked(); settle()
        assert list(window.session_rows)==['12']
        window.favorite_tab.clicked()
        window.search.set_text('project-34'); settle()
        assert list(window.session_rows)==['34']
        window.search.set_text('does-not-exist'); settle()
        assert not window.session_rows and window.empty.get_visible()
        window.select(None); settle()
        assert window.get_size().height<=collapsed
        window.preferences(); settle()
        switch=next(c for line in window.details.get_children() if isinstance(line,Gtk.Box) for c in line.get_children() if isinstance(c,Gtk.Switch))
        switch.set_active(False); settle()
        assert window.settings['compact'] is False
        assert not switch.get_state()
        window.move(-10000,-10000); settle()
        window.fit_window(snap=True); settle()
        area=window.get_display().get_monitor_at_window(window.get_window()).get_workarea()
        x,y=window.get_position()
        assert x>=area.x and y>=area.y
        window.hide(); settle()
        assert not window.get_visible()
        window.show(); settle()
        assert window.get_visible()
        window.destroy(); settle()
print('PASS: scroll/focus preserved, row identity, favorite persistence, project search, empty state, settings switches, monitor clamp, hide/show')

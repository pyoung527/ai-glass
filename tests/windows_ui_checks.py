"""Qt UI interaction check using fake data and isolated writable storage."""
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
import windows_app


def fixture():
    now=time.time()
    return {'checkedAt':now,'providers':[{'id':name,'message':'Test','sessions':[
        {'id':'session-1','title':'Example session','cwd':str(Path.home()),'updatedAt':now,'status':'open','pids':[]},
        {'id':'session-2','title':'Saved session','cwd':str(Path.home()),'updatedAt':now,'status':'saved','pids':[]}],
        'windows':[{'key':'weekly','label':'주간','remaining':73,'resetsAt':now+86400,'observedAt':now,'stale':False}]}
        for name in windows_app.NAMES]}


def check(app):
    with tempfile.TemporaryDirectory() as tmp,patch.object(windows_app,'DATA',Path(tmp)):
        window=windows_app.Glass(fixture=fixture());window.show();app.processEvents()
        assert '73%' in window.cards['codex'][1].text()
        window.cards['codex'][0].click();app.processEvents()
        assert window.detail.isVisible() and window.sessions.count()==1
        window.recent.setChecked(True);assert window.sessions.count()==2
        window.search.setText('Saved');assert window.sessions.count()==1
        window.sessions.setCurrentRow(0);window.favorite();assert window.settings['favorites']==['codex:session-2']
        with patch.object(windows_app.desktop,'open_terminal') as opened:
            window.open_session(window.sessions.item(0));opened.assert_called_once()
        window.search.setText('missing');assert window.sessions.item(0).data(Qt.UserRole) is None
        window.select(None);app.processEvents();assert not window.detail.isVisible()
        window.close()
    print('PASS: Qt quota, session filters, favorites, resume dispatch, collapse',flush=True)


if __name__=='__main__':
    app=QApplication.instance() or QApplication(sys.argv)
    check(app)

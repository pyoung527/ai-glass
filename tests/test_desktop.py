import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
try:
    import desktop
except ImportError:
    desktop=None


@unittest.skipIf(desktop is None, 'GTK/Gio required')
class DesktopChecks(unittest.TestCase):
    def test_resume_quotes_workspace_and_session(self):
        session={'cwd':"/tmp/a 'b;$(touch nope)",'id':"id'; echo wrong"}
        command=desktop.resume_command('claude',session)
        self.assertEqual(shlex.split(command),['cd',session['cwd'],'&&','claude','--resume',session['id']])
        with patch.object(desktop.subprocess,'Popen') as popen:
            with self.assertRaises(ValueError): desktop.resume('claude',session)
            popen.assert_not_called()

    def test_autostart_preserves_unrelated_file(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(desktop,'AUTOSTART',Path(tmp,'auto.desktop')):
            desktop.set_autostart(True)
            self.assertTrue(desktop.autostart_enabled())
            desktop.set_autostart(False)
            self.assertFalse(desktop.autostart_enabled())
            desktop.AUTOSTART.write_text('some other app')
            with self.assertRaises(ValueError): desktop.set_autostart(True)
            self.assertEqual(desktop.AUTOSTART.read_text(),'some other app')

    @unittest.skipUnless(os.getenv('GSETTINGS_BACKEND')=='memory','Run with memory settings backend')
    def test_shortcut_preserves_other_bindings_and_rejects_conflict(self):
        parent=desktop.keyboard_settings()
        other='/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/testing/'
        parent.set_strv('custom-keybindings',[other])
        config=desktop.Gio.Settings.new_with_path('org.gnome.settings-daemon.plugins.media-keys.custom-keybinding',other)
        config.set_string('binding',desktop.BINDING)
        with self.assertRaises(ValueError): desktop.set_shortcut(True)
        config.set_string('binding','<Super><Alt>h')
        desktop.set_shortcut(True)
        self.assertEqual(list(parent.get_strv('custom-keybindings')),[other,desktop.BINDING_PATH])
        desktop.set_shortcut(False)
        self.assertEqual(list(parent.get_strv('custom-keybindings')),[other])


if __name__=='__main__': unittest.main()

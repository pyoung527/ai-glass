import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import agent_runtime
import windows_desktop


class Portability(unittest.TestCase):
    def test_windows_terminal_does_not_expand_session_metadata(self):
        session={'id':"id'; Write-Host bad; '$(bad)%PATH%",'cwd':"C:\\User Files\\a'b"}
        with patch.object(agent_runtime,'command',return_value=['C:\\Program Files\\claude.exe','--resume',session['id']]):
            cmd=windows_desktop.terminal_command('claude',session)
        script=base64.b64decode(cmd[-1]).decode('utf-16-le')
        self.assertEqual(script,"Set-Location -LiteralPath 'C:\\User Files\\a''b' -ErrorAction Stop; & 'C:\\Program Files\\claude.exe' '--resume' 'id''; Write-Host bad; ''$(bad)%PATH%'")
        self.assertNotIn('cmd.exe',cmd)

    def test_npm_codex_uses_node_without_cmd_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim=Path(tmp)/'codex.cmd';shim.touch()
            script=Path(tmp)/'node_modules/@openai/codex/bin/codex.js';script.parent.mkdir(parents=True);script.touch()
            with patch.object(agent_runtime,'WINDOWS',True),patch.object(agent_runtime.shutil,'which',side_effect=lambda n:str(shim) if n=='codex' else 'node.exe'):
                self.assertEqual(agent_runtime.command('codex','resume','test'),['node.exe',str(script),'resume','test'])

    @unittest.skipUnless(os.name=='nt','Windows ConPTY integration')
    def test_conpty_roundtrip(self):
        import sys
        import threading
        from winpty import PtyProcess
        import subprocess
        process=PtyProcess.spawn(subprocess.list2cmdline([sys.executable,'-c','print("AI_GLASS_PTY_OK",flush=True); input()']))
        output=[]
        thread=threading.Thread(target=lambda:output.append(process.read()),daemon=True);thread.start();thread.join(timeout=10)
        try:
            self.assertTrue(output and 'AI_GLASS_PTY_OK' in output[0])
        finally:process.close(force=True)

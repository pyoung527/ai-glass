import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import os
import sys
import shlex
import collector


class DataChecks(unittest.TestCase):
    def test_quota_unknown_expiry_and_windows(self):
        result = collector.windows({'primary': {'used_percent': 14, 'window_minutes': 10080, 'resets_at': 2000}, 'secondary': None}, 990, 'Codex', now=1000)
        self.assertEqual((result[0]['remaining'], result[0]['label'], result[0]['stale']), (86, '주간', False))
        expired = collector.windows({'five_hour': {'used_percentage': 20, 'resets_at': 900}}, 890, 'Claude', now=1000)
        self.assertIsNone(expired[0]['remaining'])
        self.assertTrue(expired[0]['stale'])
        self.assertEqual(collector.windows({'primary': {'used_percent': None}}, 990, '', now=1000), [])
        self.assertEqual(collector.windows({'primary': {'used_percent': float('nan')}}, 990, '', now=1000), [])
        self.assertEqual(collector.session_status('/a/log', 'id', 0, (set(), {'/a/log'})), 'open')
        self.assertEqual(collector.session_status('/a/log', 'id', 0, (set(), set())), 'saved')

    def test_partial_logs_and_claude_bridge_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            log = root/'sample.jsonl'
            log.write_text('{"type":"event_msg"}\n{"partial":')
            self.assertEqual(list(collector.records(log)), [{'type':'event_msg'}])
            settings = root/'settings.json'
            argv=[sys.executable,'-c','import sys; sys.stdin.read(); print("previous",end="")']
            command=subprocess.list2cmdline(argv) if os.name=='nt' else shlex.join(argv)
            previous = {'statusLine': {'type':'command','command':command}, 'other':True}
            settings.write_text(json.dumps(previous))
            env = {**os.environ, 'PYTHONIOENCODING':'utf-8', 'AI_GLASS_DATA_DIR':str(root/'data'), 'CLAUDE_CONFIG_DIR':str(root)}
            script = str(collector.ROOT/'scripts/statusline_bridge.py')
            subprocess.run([sys.executable, script, '--install'], env=env, check=True, capture_output=True)
            subprocess.run([sys.executable, script, '--install'], env=env, check=True, capture_output=True)
            payload = {'rate_limits':{'five_hour':{'used_percentage':20,'resets_at':2000}}, 'secret':'must-not-be-saved'}
            result = subprocess.run([sys.executable,script], input=json.dumps(payload), text=True, env=env, capture_output=True, check=True)
            self.assertEqual(result.stdout, 'previous')
            saved = json.loads((root/'data/claude-usage.json').read_text())
            self.assertEqual(saved['rate_limits'], payload['rate_limits'])
            self.assertNotIn('secret', saved)
            diagnostics=json.loads((root/'data/claude-connection.json').read_text())
            self.assertTrue(diagnostics['hasQuota'])
            self.assertNotIn('secret',diagnostics)
            subprocess.run([sys.executable,script], input='{"version":"test"}', text=True, env=env, check=True, capture_output=True)
            self.assertFalse(json.loads((root/'data/claude-connection.json').read_text())['hasQuota'])
            self.assertEqual(json.loads((root/'data/claude-usage.json').read_text()),saved)
            # Preserve unrelated settings added while connected.
            active = json.loads(settings.read_text()); active['new'] = 'keep'; settings.write_text(json.dumps(active))
            subprocess.run([sys.executable,script,'--uninstall'], env=env, check=True, capture_output=True)
            self.assertEqual(json.loads(settings.read_text()), {**previous,'new':'keep'})
            ag_env = {**env, 'ANTIGRAVITY_CONFIG_DIR':str(root)}
            subprocess.run([sys.executable,script,'--antigravity','--install'], env=ag_env, check=True, capture_output=True)
            quota = {'quota':{'gemini-weekly':{'remaining_fraction':0.64,'reset_time':'2030-01-01T00:00:00Z'}}}
            subprocess.run([sys.executable,script,'--antigravity'], input=json.dumps(quota), text=True, env=ag_env, check=True, capture_output=True)
            saved = json.loads((root/'data/antigravity-usage.json').read_text())
            parsed = collector.windows(saved['rate_limits'],saved['observedAt'],'Antigravity')
            self.assertEqual(parsed[0]['remaining'],64)
            self.assertEqual(parsed[0]['resetsAt'],1893456000)
            subprocess.run([sys.executable,script,'--antigravity','--uninstall'], env=ag_env, check=True, capture_output=True)
            self.assertEqual(json.loads(settings.read_text()), {**previous,'new':'keep'})


if __name__ == '__main__':
    unittest.main()

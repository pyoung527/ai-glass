import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import collector
import remote_usage


class RemoteUsageChecks(unittest.TestCase):
    @unittest.skipIf(os.name=="nt", "POSIX PTY test; ConPTY is checked on Windows separately")
    def test_cli_refresh_uses_terminal_and_reaps_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'credentials.json'
            path.write_text('{}')
            cli=Path(tmp)/'claude'
            cli.write_text('#!/usr/bin/python3\nimport os,sys,json,time\n'
                'assert os.isatty(0) and sys.argv[-1]=="/usage"\n'
                f'open({str(path)!r},"w").write(json.dumps({{"claudeAiOauth":{{"accessToken":"fresh","expiresAt":4102444800000}}}}))\n'
                'time.sleep(60)\n')
            cli.chmod(0o700)
            with patch.object(remote_usage,'executable',return_value=str(cli)):
                remote_usage.refresh_claude_auth(path)
            self.assertEqual(json.loads(path.read_text())['claudeAiOauth']['accessToken'],'fresh')

    def test_expired_claude_token_is_refreshed_by_cli_before_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,CLAUDE_CONFIG_DIR=tmp):
            path=Path(tmp)/'.credentials.json'
            path.write_text(json.dumps({'claudeAiOauth':{'accessToken':'expired','expiresAt':1}}))
            def refresh(path):
                path.write_text(json.dumps({'claudeAiOauth':{'accessToken':'fresh','expiresAt':4102444800000}}))
            with patch.object(remote_usage,'refresh_claude_auth',side_effect=refresh) as cli, patch.object(remote_usage.urllib.request,'build_opener') as opener:
                opener.return_value.open.return_value.__enter__.return_value.read.return_value=b'{"five_hour":{"utilization":12}}'
                self.assertEqual(remote_usage.claude()['five_hour']['used_percentage'],12)
                cli.assert_called_once_with(path)
                request=opener.return_value.open.call_args.args[0]
                self.assertEqual(request.get_header('Authorization'),'Bearer fresh')

    def test_antigravity_marker_recovery_requires_unlocked_keyring(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,ANTIGRAVITY_CONFIG_DIR=tmp):
            marker=Path(tmp)/'cache/antigravity-keyring-unavailable'
            marker.parent.mkdir(); marker.write_text('timeout')
            with patch.object(remote_usage.subprocess,'run') as run:
                run.return_value.returncode=0
                run.return_value.stdout='(<true>,)'
                remote_usage.recover_antigravity_keyring()
                self.assertTrue(marker.exists())
                run.return_value.stdout='(<false>,)'
                remote_usage.recover_antigravity_keyring()
                self.assertFalse(marker.exists())

    def test_antigravity_quota_report_requires_both_buckets(self):
        report='Gemini Models\tWeekly Limit Remaining\t80%\t2030-01-01T00:00:00Z\nClaude and GPT models\tWeekly Limit Remaining\t49.5%\t2030-01-02T00:00:00Z\n'
        result=remote_usage.parse_antigravity(report)
        self.assertEqual(result['gemini-weekly']['used_percent'],20)
        self.assertEqual(result['3p-weekly']['used_percent'],50.5)
        with self.assertRaises(ValueError): remote_usage.parse_antigravity('model response, not usage')

    def test_failure_retains_observation_and_login_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(remote_usage,'DATA',Path(tmp)), patch.object(collector,'DATA',Path(tmp)):
            rate={'five_hour':{'used_percentage':20,'resets_at':4102444800}}
            with patch.object(remote_usage,'claude',return_value=rate):
                good=remote_usage.query('claude')
            self.assertEqual(good['state'],'ok')
            with patch.object(remote_usage,'claude',side_effect=remote_usage.LoginRequired()):
                failed=remote_usage.query('claude')
            self.assertEqual(failed['state'],'login')
            self.assertEqual(failed['windows'],good['windows'])
            provider={'id':'claude','windows':[],'sessions':[]}
            collector.apply_remote(provider)
            self.assertEqual(provider['windows'][0]['remaining'],80)
            self.assertTrue(provider['windows'][0]['stale'])
            self.assertEqual(provider['action'],'login')
            with patch.object(remote_usage,'claude',return_value=rate): remote_usage.query('claude')
            collector.apply_remote(provider)
            self.assertFalse(provider['windows'][0]['stale'])
            self.assertNotIn('action',provider)

    def test_rate_limit_blocks_repeated_queries(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(remote_usage,'DATA',Path(tmp)):
            with patch.object(remote_usage,'claude',side_effect=remote_usage.RateLimited()) as query:
                first=remote_usage.query('claude')
                second=remote_usage.query('claude')
                self.assertEqual(first,second)
                query.assert_called_once()
                self.assertEqual(second['state'],'error')

    def test_errors_do_not_expose_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(remote_usage,'DATA',Path(tmp)):
            with patch.object(remote_usage,'claude',side_effect=ValueError('secret-token')):
                result=remote_usage.query('claude')
            self.assertEqual(result['state'],'error')
            self.assertNotIn('secret-token',json.dumps(result))

if __name__=='__main__': unittest.main()

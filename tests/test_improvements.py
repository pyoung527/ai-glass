import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import collector
from usage_state import UsageState


class Improvements(unittest.TestCase):
    def snapshot(self, remaining=19, stamp=1000, reset=3000, stale=False):
        return {'providers':[{'id':'claude','windows':[{'key':'five_hour','label':'5시간','remaining':remaining,'observedAt':stamp,'resetsAt':reset,'stale':stale}]}]}

    def test_corrupt_provider_does_not_stop_others_and_retains_last_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'claude-usage.json').write_text('[]')
            good={'id':'codex','sessions':[],'windows':[],'message':'ok'}
            old=self.snapshot()
            with patch.object(collector,'DATA',Path(tmp)), patch.object(collector,'HOME',Path(tmp)), patch.object(collector,'live_sessions',return_value=(set(),set())), patch.object(collector,'codex',return_value=good), patch.object(collector,'antigravity',return_value={**good,'id':'antigravity'}), self.assertLogs(level='ERROR'):
                result=collector.collect(old)
            self.assertEqual(len(result['providers']),3)
            self.assertEqual(result['providers'][0],good)
            failed=result['providers'][1]
            self.assertTrue(failed['error'])
            self.assertEqual(failed['windows'][0]['remaining'],19)
            self.assertTrue(failed['windows'][0]['stale'])

    def test_alert_thresholds_survive_restart_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp,'history.json')
            state=UsageState(path)
            self.assertEqual(len(state.observe(self.snapshot(),True,1000)),1)
            state=UsageState(path)
            self.assertEqual(state.observe(self.snapshot(),True,1000),[])
            self.assertEqual(len(state.observe(self.snapshot(9,1001),True,1001)),1)
            self.assertEqual(state.observe(self.snapshot(8,1002,stale=True),True,1002),[])
            self.assertEqual(state.observe(self.snapshot(8,1002),True,4000),[])
            self.assertEqual(len(state.observe(self.snapshot(9,4000,6000),True,4000)),1)
            # No duplicate sampling each time the same local data is polled.
            points=state.series('claude',self.snapshot()['providers'][0]['windows'][0],4000)
            self.assertEqual([p['time'] for p in points],[1000,1001,4000])
            self.assertEqual(state.observe(self.snapshot(50,4001,6000),True,4001),[])
            self.assertEqual(len(state.observe(self.snapshot(19,4002,6000),True,4002)),1)

    def test_disabled_alerts_and_unknown_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=UsageState(Path(tmp,'history.json'))
            self.assertEqual(state.observe(self.snapshot(9),False,1000),[])
            self.assertEqual(len(state.observe(self.snapshot(9),True,1000)),1)
            self.assertEqual(state.observe(self.snapshot(None,1001),True,1001),[])
            self.assertEqual(len(state.data['history']['claude:five_hour']),1)

    def test_reset_iso_and_schema_validation(self):
        w=collector.windows({'five_hour':{'used_percentage':12,'resets_at':'2030-01-01T00:00:00Z'}},990,'Claude',1000)[0]
        self.assertEqual(w['resetsAt'],1893456000)
        self.assertEqual(w['key'],'five_hour')
        self.assertFalse(w['stale'])
        self.assertEqual(collector.windows([],990,'Claude',1000),[])
        self.assertTrue(collector.windows({'a':{'used_percent':10}},2000,'Claude',1000)[0]['stale'])


if __name__=='__main__': unittest.main()

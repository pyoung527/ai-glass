"""Local observation history and deduplicated low-quota notifications."""
import logging
import math
import time
from collector import read_json
from scripts.statusline_bridge import write_json


class UsageState:
    def __init__(self, path):
        self.path = path
        try:
            self.data = read_json(path, {})
            if not all(isinstance(self.data.get(k, {}), dict) for k in ('history', 'alerts')):
                raise ValueError('Invalid history')
            for points in self.data.get('history', {}).values():
                if not isinstance(points, list) or any(not isinstance(p, dict) or not isinstance(p.get('time'), (int,float)) or not math.isfinite(p['time']) or not isinstance(p.get('remaining'), (int,float)) or not 0 <= p['remaining'] <= 100 for p in points):
                    raise ValueError('Invalid observations')
            for alert in self.data.get('alerts', {}).values():
                if not isinstance(alert, dict) or not isinstance(alert.get('sent', []), list) or any(t not in (10,20) for t in alert.get('sent', [])):
                    raise ValueError('Invalid alerts')
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault('history', {})
        self.data.setdefault('alerts', {})

    def observe(self, snapshot, enabled=False, now=None):
        now = time.time() if now is None else now
        changed, notifications = False, []
        for provider in snapshot['providers']:
            if provider.get('error'):
                continue
            for window in provider['windows']:
                remaining = window.get('remaining')
                stamp = window.get('observedAt', 0)
                if remaining is None or window.get('stale') or not 0 <= now-stamp <= 900:
                    continue
                key = provider['id']+':'+window.get('key', window['label'])
                points = self.data['history'].setdefault(key, [])
                if not points or stamp > points[-1]['time']:
                    points.append({'time':stamp, 'remaining':remaining, 'reset':window.get('resetsAt')})
                    self.data['history'][key] = [p for p in points if p['time'] >= now-30*86400][-2000:]
                    changed = True
                if not enabled:
                    continue
                epoch = window.get('resetsAt')
                state = self.data['alerts'].get(key, {})
                sent = state.get('sent', []) if state.get('reset') == epoch else []
                sent = [threshold for threshold in sent if remaining <= threshold]
                crossed = [t for t in (20, 10) if remaining <= t and t not in sent]
                if crossed:
                    notifications.append((provider['id'], window['label'], remaining))
                    sent.extend(crossed)
                new = {'reset':epoch, 'sent':sent}
                if new != state:
                    self.data['alerts'][key] = new
                    changed = True
        if changed:
            try:
                write_json(self.path, self.data)
            except OSError:
                logging.exception('Cannot save usage history')
        return notifications

    def series(self, provider, window, now=None):
        now = time.time() if now is None else now
        key = provider+':'+window.get('key', window['label'])
        return [p for p in self.data['history'].get(key, []) if p['time'] >= now-7*86400]

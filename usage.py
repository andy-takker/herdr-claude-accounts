"""Filter Claude's usage response and cache it without storing credentials or activity."""
from datetime import datetime
import math
import time
import uuid

from accounts import AccountError


def cached_usage(store, claude, account_id, auth, force=False):
    if auth.get('state') != 'saved':
        return {'state': 'unavailable', 'reason': auth.get('state', 'unknown'), 'windows': []}
    key = account_id or 'system'
    identity = [auth.get('email'), auth.get('orgId'), claude.binary]
    now = int(time.time())
    with store.locked():
        records = store._load('usage.json', {})
        previous = records.get(key, {})
        if previous.get('identity') != identity:
            previous = {}
        if not force and 0 <= now - previous.get('attempted_at', 0) < 300:
            return previous['result']
        result = previous.get('result', {'state': 'unavailable', 'reason': 'refreshing', 'windows': []})
        # Reserve the poll before releasing the lock so two windows do not double-poll.
        request_id = uuid.uuid4().hex
        records[key] = {'identity': identity, 'attempted_at': now, 'result': result, 'request_id': request_id}
        store._write('usage.json', records)
    try:
        fresh = claude.usage(store.profile(account_id) if account_id else None)
    except (AccountError, OSError):
        fresh = {'state': 'unavailable', 'reason': 'unavailable', 'windows': [], 'checked_at': now}
    if fresh['state'] != 'available' and result.get('windows'):
        fresh = dict(result, state='stale', reason=fresh.get('reason', 'unknown'))
    with store.locked():
        records = store._load('usage.json', {})
        cancelled = getattr(claude, 'cancel', None)
        cancelled = cancelled is not None and cancelled.is_set()
        if records.get(key, {}).get('request_id') == request_id:
            if cancelled:
                # Navigation cancels probes; it must not start a five-minute failure cache.
                if previous:
                    records[key] = previous
                else:
                    records.pop(key, None)
            else:
                records[key]['result'] = fresh
            store._write('usage.json', records)
        if cancelled:
            return previous.get('result', {'state': 'unavailable', 'reason': 'cancelled', 'windows': []})
    return fresh


def invalidate(store, account_id):
    with store.locked():
        records = store._load('usage.json', {})
        if records.pop(account_id or 'system', None) is not None:
            store._write('usage.json', records)


def normalize(data):
    result = {'state': 'unavailable', 'source': 'claude', 'checked_at': int(time.time()),
              'windows': [], 'reason': 'invalid_response'}
    if not isinstance(data, dict):
        return result
    if data.get('rate_limits_available') is False:
        result['reason'] = 'not_available'
        return result
    limits = data.get('rate_limits')
    rows = limits.get('limits') if isinstance(limits, dict) else None
    if not isinstance(rows, list) or not rows:
        return result
    windows = []
    for row in rows:
        if not isinstance(row, dict):
            return result
        percent = row.get('percent')
        if type(percent) not in (int, float) or not 0 <= percent <= 100 or not math.isfinite(percent):
            return result
        group = row.get('group')
        if not isinstance(group, str):
            return result
        label = {'session': '5 hours', 'weekly': 'Weekly'}.get(group, 'Limit')
        scope = row.get('scope')
        model = scope.get('model') if isinstance(scope, dict) else None
        name = model.get('display_name') if isinstance(model, dict) else None
        if isinstance(name, str) and name.isprintable() and len(name) <= 80:
            label += ' · ' + name
        reset = row.get('resets_at')
        if reset is not None:
            try:
                parsed = datetime.fromisoformat(reset.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    return result
                reset = int(parsed.timestamp())
            except (AttributeError, ValueError, TypeError, OverflowError):
                return result
        windows.append({'label': label, 'used_percent': percent, 'resets_at': reset})
    result.update(state='available', windows=windows)
    result.pop('reason')
    return result

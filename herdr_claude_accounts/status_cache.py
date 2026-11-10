"""Share short-lived native status probes and reject results from an older login."""
import fcntl
import os
import time
import uuid

TTL = 15


def cancelled(claude):
    event = getattr(claude, 'cancel', None)
    return event is not None and event.is_set()


def records(store):
    data = store._load('status.json', {})
    return data if isinstance(data, dict) else {}


def record(data, key):
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}


def row(account, cached):
    return dict(account, auth=cached.get('auth', {}), online=cached.get('online', {}),
                sessions=cached.get('sessions', {'available': False, 'sessions': [], 'reason': 'refreshing'}))


def identity(auth):
    return tuple(auth.get(k) for k in ('state', 'email', 'orgId'))


def save_auth(cached, auth):
    previous = cached.get('auth', {})
    if auth.get('state') != 'unknown' and identity(previous) != identity(auth):
        cached['generation'] = uuid.uuid4().hex
        cached['online'] = {}
    cached['auth'] = auth


def invalidate_login(store, account_id, auth=None):
    with store.locked():
        store._profile(account_id, store._state())
        data = records(store)
        cached = record(data, account_id)
        cached.update(generation=uuid.uuid4().hex, online={})
        cached.pop('auth_at', None)
        cached.pop('sessions_at', None)
        if auth is not None:
            cached['auth'] = auth
        data[account_id] = cached
        store._write('status.json', data)


def refresh(store, claude, account, local=True):
    account_id = account['id']
    key = account_id or 'system'
    profile = store.profile(account_id) if account_id else None
    with store.locked():
        cached = record(records(store), key)
        # OS locks release on process death; a crashed poll cannot reserve a stale lease.
        fd = os.open(store.root / ('status-' + key + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return row(account, cached)
        with store.locked():
            cached = record(records(store), key)
        fields = ('sessions_at', 'auth_at') if local else ('sessions_at',)
        if cached.get('binary') == claude.binary and all(
                0 <= time.time() - cached.get(k, 0) < TTL for k in fields):
            return row(account, cached)
        generation = cached.get('generation')
        if cancelled(claude):
            return row(account, cached)
        sessions = claude.sessions(profile)
        if cancelled(claude):
            return row(account, cached)
        auth = claude.auth(profile) if local else None
        if cancelled(claude):
            return row(account, cached)
        with store.locked():
            if account_id:
                store._profile(account_id, store._state())
            data = records(store)
            current = record(data, key)
            if current.get('generation') != generation or cancelled(claude):
                return row(account, current)
            if current.get('binary') != claude.binary:
                current.pop('auth_at', None)
                current['online'] = {}
            current.update(sessions=sessions, sessions_at=time.time(), binary=claude.binary)
            if local:
                save_auth(current, auth)
                current['auth_at'] = time.time()
            data[key] = current
            store._write('status.json', data)
            return row(account, current)


def check(store, claude, account_id):
    profile = store.profile(account_id)
    with store.locked():
        generation = record(records(store), account_id).get('generation')
    auth = claude.auth(profile)
    unavailable = {'state': 'unknown', 'source': 'online', 'reason': 'account_changed', 'checked_at': int(time.time())}
    if cancelled(claude):
        return dict(unavailable, reason='cancelled')
    with store.locked():
        data = records(store)
        cached = record(data, account_id)
        if cached.get('generation') != generation:
            return unavailable
        save_auth(cached, auth)
        generation = cached.get('generation')
        data[account_id] = cached
        store._write('status.json', data)
    result = claude.check(profile)
    if cancelled(claude):
        return dict(unavailable, reason='cancelled')
    current_auth = claude.auth(profile)
    if cancelled(claude):
        return dict(unavailable, reason='cancelled')
    with store.locked():
        store._profile(account_id, store._state())
        data = records(store)
        cached = record(data, account_id)
        if cached.get('generation') != generation:
            return unavailable
        if identity(current_auth) != identity(auth):
            if current_auth.get('state') in ('saved', 'provider', 'needs_login'):
                save_auth(cached, current_auth)
                cached['auth_at'] = time.time()
                data[account_id] = cached
                store._write('status.json', data)
            return unavailable
        if identity(cached.get('auth', {})) != identity(auth):
            return unavailable
        cached['online'] = result
        data[account_id] = cached
        store._write('status.json', data)
    return result

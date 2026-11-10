"""Cancel a specific browser sign-in by a private ticket, never by a stored PID."""
from contextlib import contextmanager
import time
import uuid

from accounts import AccountError


class LoginAttempt:
    def __init__(self, store, identity):
        try:
            if str(uuid.UUID(identity)) != identity:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise AccountError('Invalid login attempt.') from None
        self.store = store
        self.identity = identity
        self.name = 'login-' + identity + '.json'

    @classmethod
    def prepare(cls, store, account_id):
        with store.locked():
            store._profile(account_id, store._state())
            attempt = cls(store, str(uuid.uuid4()))
            store._write(attempt.name, {'account': account_id, 'state': 'pending',
                                        'expires_at': time.time() + 600})
            return {'attempt': attempt.identity}

    def cancel(self):
        with self.store.locked():
            record = self.store._load(self.name, None)
            if not record:
                return {'cancelled': False}
            record['state'] = 'cancelled'
            self.store._write(self.name, record)
            return {'cancelled': True}

    def is_set(self):
        with self.store.locked():
            record = self.store._load(self.name, None)
            return not record or record.get('state') == 'cancelled' or record.get('expires_at', 0) < time.time()

    @contextmanager
    def running(self, account_id):
        with self.store.locked():
            record = self.store._load(self.name, None)
            if not isinstance(record, dict) or record.get('account') != account_id or record.get('state') not in ('pending', 'cancelled'):
                raise AccountError('Login attempt is missing, already running, or belongs to another profile.')
            if record['state'] == 'pending':
                record['state'] = 'running'
                self.store._write(self.name, record)
        try:
            yield self
        finally:
            with self.store.locked():
                (self.store.root / self.name).unlink(missing_ok=True)

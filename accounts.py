"""Private Claude profiles and atomic, process-safe selection."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import uuid


class AccountError(Exception):
    pass


def default_root():
    return Path.home() / '.local/share/herdr-claude-accounts'


class Store:
    def __init__(self, root=None):
        self.root = Path(root or default_root()).absolute()

    @contextmanager
    def locked(self):
        for p in (self.root, self.root / 'profiles'):
            if p.is_symlink():
                raise AccountError('Data directories must not be symbolic links.')
            p.mkdir(mode=0o700, parents=True, exist_ok=True)
            p.chmod(0o700)
        fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _load(self, name, default):
        path = self.root / name
        if path.is_symlink():
            raise AccountError('Metadata must not be a symbolic link.')
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return default
        except (ValueError, OSError):
            raise AccountError('Cannot read ' + name + '; restore it from a backup.') from None

    def _write(self, name, value):
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix='.write-')
        try:
            with os.fdopen(fd, 'w') as out:
                json.dump(value, out, ensure_ascii=False, indent=2)
                out.write('\n')
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, self.root / name)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _state(self):
        state = self._load('accounts.json', {
            'version': 1, 'accounts': [], 'selected': None, 'binary': None
        })
        try:
            if not isinstance(state, dict) or state['version'] != 1 or not isinstance(state['accounts'], list):
                raise ValueError()
            ids = []
            for a in state['accounts']:
                if not isinstance(a, dict) or str(uuid.UUID(a['id'])) != a['id']:
                    raise ValueError()
                self.validate_label(a['label'])
                ids.append(a['id'])
            if len(ids) != len(set(ids)) or (state['selected'] is not None and state['selected'] not in ids):
                raise ValueError()
            if state['binary'] is not None and not (
                isinstance(state['binary'], str) and Path(state['binary']).is_absolute()
            ):
                raise ValueError()
            if 'share_conversations' in state and not isinstance(state['share_conversations'], bool):
                raise ValueError()
            shared = state.get('shared_settings')
            if shared is not None and (not isinstance(shared, dict) or any(
                    not isinstance(shared.get(k), str) or not Path(shared[k]).is_absolute()
                    for k in ('source', 'state'))):
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AccountError('Invalid accounts.json; refusing to choose another account.') from None
        return state

    @staticmethod
    def validate_label(label):
        if not isinstance(label, str) or not label.strip() or len(label) > 80 or not label.isprintable():
            raise AccountError('Use a printable account name of 1–80 characters.')

    def read(self):
        with self.locked():
            return self._state()

    def create(self, label):
        self.validate_label(label)
        with self.locked():
            state = self._state()
            account = {'id': str(uuid.uuid4()), 'label': label.strip()}
            (self.root / 'profiles' / account['id']).mkdir(mode=0o700)
            state['accounts'].append(account)
            self._write('accounts.json', state)
        try:
            self.sync_settings()
        except (AccountError, OSError) as error:
            detail = str(error) if isinstance(error, AccountError) else 'Check file permissions.'
            return dict(account, warning='Profile created and retained, but settings could not sync: ' + detail)
        return account

    def _profile(self, account_id, state):
        if account_id not in [a['id'] for a in state['accounts']]:
            raise AccountError('Unknown account ID.')
        path = self.root / 'profiles' / account_id
        if path.is_symlink() or not path.is_dir():
            raise AccountError('Selected profile is missing or is a symbolic link; launch cancelled.')
        return path

    def profile(self, account_id):
        with self.locked():
            return self._profile(account_id, self._state())

    def select(self, account_id):
        self.sync_settings()
        with self.locked():
            state = self._state()
            if account_id is not None:
                self._profile(account_id, state)
            state['selected'] = account_id
            self._write('accounts.json', state)

    def remove(self, account_id):
        with self.locked():
            state = self._state()
            profile = self._profile(account_id, state)
            account = next(a for a in state['accounts'] if a['id'] == account_id)
            record = 'removed-' + account_id + '.json'
            # Keep the original path so running Claude sessions and shared links remain intact.
            self._write(record, {'account': account, 'profile': str(profile),
                                 'removed_at': int(time.time())})
            state['accounts'] = [a for a in state['accounts'] if a['id'] != account_id]
            state['selected'] = None
            self._write('accounts.json', state)
            return {'removed': account_id, 'selected': None, 'profile_retained': str(profile),
                    'record': str(self.root / record)}

    def enable_shared_settings(self, home=None):
        from shared_settings import synchronize
        home = Path(home or Path.home()).absolute()
        source = home / '.claude'
        config = source / '.config.json'
        shared = {'source': str(source), 'state': str(config if config.exists() else home / '.claude.json')}
        existing = self.read().get('shared_settings')
        shared = existing or shared
        result = synchronize(self, shared['source'], shared['state'])
        with self.locked():
            state = self._state()
            state['shared_settings'] = shared
            self._write('accounts.json', state)
        return result

    def _removed(self, state):
        active = {a['id'] for a in state['accounts']}
        result = []
        for path in sorted(self.root.glob('removed-*.json')):
            account_id = path.name[len('removed-'):-len('.json')]
            if account_id in active:
                continue
            record = self._load(path.name, None)
            try:
                if str(uuid.UUID(account_id)) != account_id or record['account']['id'] != account_id:
                    raise ValueError()
                self.validate_label(record['account']['label'])
                if record['profile'] != str(self.root / 'profiles' / account_id):
                    raise ValueError()
                if not isinstance(record['removed_at'], int):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, AttributeError):
                raise AccountError('Invalid removed account record: ' + path.name) from None
            result.append(record)
        return result

    def removed(self):
        with self.locked():
            return self._removed(self._state())

    def restore(self, account_id):
        with self.locked():
            state = self._state()
            record = next((r for r in self._removed(state) if r['account']['id'] == account_id), None)
            if record is None:
                raise AccountError('Unknown removed account ID.')
            account = record['account']
            state['accounts'].append(account)
            self._profile(account_id, state)
            self._write('accounts.json', state)
        try:
            self.sync_settings()
        except AccountError as error:
            raise AccountError('Profile restored, but shared settings could not sync: ' + str(error)) from None
        except OSError:
            raise AccountError('Profile restored, but shared settings could not sync; check file permissions.') from None
        return account

    def enable_shared_conversations(self):
        from shared_settings import synchronize
        shared = self.read().get('shared_settings')
        if not shared:
            raise AccountError('Enable shared settings before sharing conversation history.')
        result = synchronize(self, shared['source'], shared['state'], share_conversations=True)
        with self.locked():
            state = self._state()
            state['share_conversations'] = True
            self._write('accounts.json', state)
        return result

    def sync_settings(self):
        from shared_settings import synchronize
        state = self.read()
        shared = state.get('shared_settings')
        if shared:
            return synchronize(self, shared['source'], shared['state'],
                               share_conversations=state.get('share_conversations', False))

    def configure_binary(self, binary):
        path = Path(binary).absolute()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise AccountError('Claude executable is missing or not executable.')
        with self.locked():
            state = self._state()
            state['binary'] = str(path)
            self._write('accounts.json', state)

    def cache(self, account_id, kind, value):
        with self.locked():
            self._profile(account_id, self._state())
            data = self._load('status.json', {})
            if not isinstance(data, dict):
                data = {}
            if not isinstance(data.get(account_id), dict):
                data[account_id] = {}
            data[account_id][kind] = value
            self._write('status.json', data)

    def cached(self):
        with self.locked():
            data = self._load('status.json', {})
            return data if isinstance(data, dict) else {}

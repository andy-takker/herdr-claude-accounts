"""Share settings and conversation context while keeping account identity private."""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

from herdr_claude_accounts.accounts import AccountError

DIRECTORIES = ('skills', 'agents', 'commands', 'hooks', 'scripts', 'plugins',
               'output-styles', 'themes', 'rules')
JSON_FILES = ('settings.json', 'settings.local.json', 'keybindings.json')
PREFERENCES = {
    'theme', 'editorMode', 'preferredNotifChannel', 'verbose', 'autoUpdates',
    'hasCompletedOnboarding', 'lastOnboardingVersion', 'hasIdeOnboardingBeenShown',
    'hasCompletedClaudeInChromeOnboarding', 'claudeInChromeDefaultEnabled',
    'chromeExtension', 'shiftEnterKeyBindingInstalled', 'hasUsedBackslashReturn',
    'showSpinnerTree', 'deepLinkTerminal', 'hasSeenAutoDefaultNotice',
    'hasSeenAutoModeOutsideReadPrompt', 'autoModeEnvSetup', 'mcpServers',
}
PROJECT_SETTINGS = {
    'allowedTools', 'mcpServers', 'mcpContextUris', 'enabledMcpjsonServers',
    'disabledMcpjsonServers', 'disabledMcpServers', 'hasTrustDialogAccepted',
    'hasClaudeMdExternalIncludesApproved', 'hasClaudeMdExternalIncludesWarningShown',
    'hasCompletedProjectOnboarding', 'projectOnboardingSeenCount',
}
MISSING = object()


def read_bytes(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        if path.is_symlink():
            raise AccountError('Broken settings link: ' + str(path)) from None
        return None


def json_object(raw, path):
    try:
        value = json.loads(raw) if raw is not None else {}
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise AccountError('Invalid settings JSON: ' + str(path)) from None


def shared_state(value):
    result = {k: v for k, v in value.items() if k in PREFERENCES}
    projects = value.get('projects', {})
    if not isinstance(projects, dict):
        raise AccountError('Claude project settings must be an object.')
    shared = {}
    for path, entry in projects.items():
        if not isinstance(entry, dict):
            raise AccountError('Invalid Claude project settings entry.')
        fields = {k: v for k, v in entry.items() if k in PROJECT_SETTINGS}
        if fields:
            shared[path] = fields
    if shared:
        result['projects'] = shared
    return result


def replace_shared_state(current, shared):
    result = {k: v for k, v in current.items() if k not in PREFERENCES and k != 'projects'}
    result.update({k: v for k, v in shared.items() if k != 'projects'})
    projects = {}
    for path, entry in current.get('projects', {}).items():
        private = {k: v for k, v in entry.items() if k not in PROJECT_SETTINGS}
        if private:
            projects[path] = private
    for path, entry in shared.get('projects', {}).items():
        projects.setdefault(path, {}).update(entry)
    if projects or 'projects' in current:
        result['projects'] = projects
    return result


def flatten(value, prefix=()):
    result = {}
    for key, entry in value.items():
        path = prefix + (key,)
        if isinstance(entry, dict) and entry:
            result.update(flatten(entry, path))
        else:
            result[json.dumps(path, ensure_ascii=False)] = entry
    return result


def expand(flat):
    result = {}
    for key, value in sorted(flat.items()):
        path = json.loads(key)
        current = result
        for part in path[:-1]:
            if part in current and not isinstance(current[part], dict):
                raise AccountError('Conflicting settings structure; reconcile edits before launching.')
            current = current.setdefault(part, {})
        if path[-1] in current:
            raise AccountError('Conflicting settings structure; reconcile edits before launching.')
        current[path[-1]] = value
    return result


def digest(value):
    if value is MISSING:
        return None
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reconcile(values, previous, known, name):
    """Initial import prefers the base; later differing edits fail without losing either."""
    if previous is None:
        merged, leaves, parents = {}, set(), set()
        for value in values:
            for key, entry in value.items():
                path = tuple(json.loads(key)) if key.startswith('[') else (key,)
                if path in parents or any(path[:n] in leaves for n in range(1, len(path) + 1)):
                    continue
                merged[key] = entry
                leaves.add(path)
                parents.update(path[:n] for n in range(1, len(path)))
        return merged
    merged = {}
    keys = set(previous)
    for value in values:
        keys.update(value)
    for key in keys:
        changes = {}
        for index, value in enumerate(values):
            if index and index not in known:
                continue
            entry = value.get(key, MISSING)
            hashed = digest(entry)
            if hashed != previous.get(key):
                changes[hashed] = entry
        if len(changes) > 1:
            raise AccountError('Conflicting edits in ' + name + ' at ' + key +
                               '; set the same value in the affected profiles and retry. Files unchanged.')
        chosen = next(iter(changes.values())) if changes else values[0].get(key, MISSING)
        if chosen is not MISSING:
            merged[key] = chosen
    return merged


@contextmanager
def claude_lock(path):
    # Claude uses proper-lockfile's mkdir protocol for its mixed auth/preferences file.
    lock = Path(str(path.resolve()) + '.lock')
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        before = lock.stat()
        if time.time() - before.st_mtime <= 60 or lock.stat() != before:
            raise AccountError('Claude is updating ' + str(path) + '; retry in a moment.') from None
        try:
            lock.rmdir()
            lock.mkdir(mode=0o700)
        except OSError:
            raise AccountError('Settings lock is busy: ' + str(lock)) from None
    inode = lock.stat().st_ino
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(2):
            try:
                if lock.stat().st_ino != inode:
                    return
                os.utime(lock, None)
            except OSError:
                return

    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join()
        if lock.exists() and lock.stat().st_ino == inode:
            lock.rmdir()


def atomic_write(path, data):
    target = path.resolve() if path.is_symlink() else path
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
    fd, name = tempfile.mkstemp(dir=target.parent, prefix='.account-settings-')
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, mode)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def synchronize(store, source, source_state, *, share_conversations=False):
    source, source_state = Path(source).absolute(), Path(source_state).absolute()
    if source.resolve().is_relative_to(store.root.resolve()) or store.root.resolve().is_relative_to(source.resolve()):
        raise AccountError('Shared settings source must be outside managed account data.')
    source.mkdir(mode=0o700, parents=True, exist_ok=True)
    with store.locked(), ExitStack() as locks:
        state = store._state()
        ids = [a['id'] for a in state['accounts']]
        homes = [source] + [store._profile(i, state) for i in ids]
        states = [source_state] + [p / ('.config.json' if (p / '.config.json').exists() else '.claude.json') for p in homes[1:]]
        if any(p.is_symlink() for p in states[1:]):
            raise AccountError('Account identity file must not be a symbolic link.')
        record = store._load('settings-sync.json', {})
        if not isinstance(record, dict) or (record and record.get('version') != 1):
            raise AccountError('Invalid settings synchronization record.')
        if record and record.get('source') != str(source):
            raise AccountError('Shared settings source changed; restore the original source path.')
        known = {n + 1 for n, i in enumerate(ids) if i in record.get('profiles', [])}
        surfaces = {'state': states}
        surfaces.update({name: [p / name for p in homes] for name in JSON_FILES})
        markdown = {p.name for home in homes for p in home.glob('*.md') if p.is_file()}
        surfaces.update({name: [p / name for p in homes] for name in markdown})
        for path in sorted({p.resolve() for p in states}):
            locks.enter_context(claude_lock(path))

        links = []
        for account_id, home in zip(ids, homes[1:]):
            for name in DIRECTORIES:
                src, dst = source / name, home / name
                if src.exists() and not src.is_dir():
                    raise AccountError('Shared extension path is not a directory: ' + str(src))
                if src.is_symlink() and not src.exists():
                    raise AccountError('Shared extension link is broken: ' + str(src))
                if dst.is_symlink():
                    if dst.resolve() != src.resolve():
                        raise AccountError('Profile has a different extension link: ' + str(dst))
                else:
                    if dst.exists() and not dst.is_dir():
                        raise AccountError('Profile extension path is not a directory: ' + str(dst))
                    links.append((account_id, src, dst))

        originals, writes, hashes = {}, {}, {}
        for name, paths in surfaces.items():
            values, documents = [], []
            for path in paths:
                raw = read_bytes(path)
                originals[path] = raw
                if name == 'state' or name in JSON_FILES:
                    doc = json_object(raw, path)
                    documents.append(doc)
                    value = shared_state(doc) if name == 'state' else doc
                    if name in ('settings.json', 'settings.local.json'):
                        from herdr_claude_accounts.claude_cli import AUTH_ENV
                        env = value.get('env', {})
                        if not isinstance(env, dict) or value.get('apiKeyHelper') or any(env.get(k) for k in AUTH_ENV):
                            raise AccountError('Shared settings contain an authentication override; keep account credentials separate.')
                    values.append(flatten(value))
                else:
                    try:
                        values.append({'content': raw.decode('utf-8')} if raw is not None else {})
                    except UnicodeError:
                        raise AccountError('Instruction file is not UTF-8: ' + str(path)) from None
            merged = reconcile(values, record.get('files', {}).get(name), known, name)
            hashes[name] = {key: digest(value) for key, value in merged.items()}
            if name == 'state' or name in JSON_FILES:
                common = expand(merged)
                for path, doc in zip(paths, documents):
                    desired = replace_shared_state(doc, common) if name == 'state' else common
                    if desired != doc:
                        writes[path] = (json.dumps(desired, ensure_ascii=False, indent=2) + '\n').encode()
            else:
                data = merged.get('content', MISSING)
                for path in paths:
                    # Missing text files become empty; no existing instructions are silently resurrected.
                    desired = data.encode() if data is not MISSING else b''
                    if desired != (originals[path] or b''):
                        writes[path] = desired

        for path, raw in originals.items():
            if read_bytes(path) != raw:
                raise AccountError('Settings changed during synchronization; retry the launch.')

        backup = None
        def backup_path(path, label):
            nonlocal backup
            if backup is None:
                backup = store.root / 'settings-backups' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
                backup.mkdir(parents=True, mode=0o700)
                backup.parent.chmod(0o700)
            dest = backup / label / path.name
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            return dest

        conversations = {}
        if share_conversations:
            from herdr_claude_accounts.shared_conversations import prepare
            conversations = prepare(homes, backup_path)()

        for path, raw in writes.items():
            if originals[path] is not None:
                label = 'system' if path.parent == source or path == source_state else path.parent.name
                dest = backup_path(path, label)
                dest.write_bytes(originals[path])
                dest.chmod(0o600)
            atomic_write(path, raw)
        for account_id, src, dst in links:
            src.mkdir(mode=0o700, parents=True, exist_ok=True)
            if dst.exists():
                dst.rename(backup_path(dst, account_id))
            dst.symlink_to(src, target_is_directory=True)
        next_record = {'version': 1, 'source': str(source), 'profiles': ids, 'files': hashes}
        if record != next_record:
            store._write('settings-sync.json', next_record)
        return {'profiles': len(ids), 'source': str(source),
                'backup': str(backup) if backup else None, 'updated_files': len(writes),
                **conversations}

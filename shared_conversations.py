"""Unite resumable Claude context without sharing account or process identity."""
import filecmp
import json
import os
import shutil
import stat

from accounts import AccountError

DIRECTORIES = ('projects', 'file-history', 'paste-cache', 'plans', 'tasks',
               'todos', 'agent-memory', 'image-cache', 'uploads')


def active(home):
    for record in (home / 'sessions').glob('*.json'):
        if record.stem.isdigit() and int(record.stem) > 0:
            try:
                os.kill(int(record.stem), 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                pass
            return True
    return False


def signature(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def inventory(root):
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise AccountError('Conversation path must be a real directory: ' + str(root))
    result = {}
    if root.exists():
        for path in root.rglob('*'):
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise AccountError('Unsupported conversation file or symbolic link: ' + str(path))
            result[path.relative_to(root)] = path
    return result


def history_bytes(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise AccountError('History must be a regular file: ' + str(path))
    raw = path.read_bytes() if path.exists() else b''
    try:
        if raw and not raw.endswith(b'\n'):
            raise ValueError()
        if any(not isinstance(json.loads(line), dict) for line in raw.splitlines() if line.strip()):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise AccountError('History is incomplete or invalid; retry after Claude finishes writing: ' + str(path)) from None
    return raw


def prepare(homes, backup_path):
    """Validate the complete merge before any settings or conversation mutation."""
    source, profiles = homes[0], homes[1:]
    links, imports, extensions, observed = [], {}, {}, {}
    pending = []

    def observe(path):
        observed[path] = signature(path)

    for home in profiles:
        for name in (*DIRECTORIES, 'history.jsonl'):
            staging = home / ('.sharing-' + name)
            if staging.exists() or staging.is_symlink():
                raise AccountError('Unfinished conversation migration; original retained at: ' + str(staging))
        unshared = any(not (home / name).is_symlink() for name in (*DIRECTORIES, 'history.jsonl'))
        busy = unshared and active(home)
        if busy:
            # shortcut: live profiles import on sync; finish links after their sessions exit.
            pending.append(home.name)
        for name in DIRECTORIES:
            dst, src = home / name, source / name
            if dst.is_symlink():
                if (dst.resolve() != src.resolve() or src.is_symlink()
                        or (src.exists() and not src.is_dir())):
                    raise AccountError('Profile has a different conversation link: ' + str(dst))
                if not src.exists():
                    observe(src)
                    imports.setdefault(src, {})
                continue
            # Only inspect shared trees when importing; normal launches are constant-time.
            if src not in imports:
                imports[src] = inventory(src)
            entries = inventory(dst)
            observe(dst)
            for relative, path in entries.items():
                target = src / relative
                previous = imports[src].get(relative)
                observe(path)
                if previous is None:
                    imports[src][relative] = path
                elif path.is_dir() != previous.is_dir():
                    raise AccountError('Conflicting conversation file types: ' + str(target))
                elif path.is_file() and not os.path.samefile(path, previous):
                    observe(previous)
                    if filecmp.cmp(path, previous, shallow=False):
                        continue
                    old = extensions.get(target, previous.read_bytes())
                    new = path.read_bytes()
                    if name == 'projects' and path.suffix == '.jsonl' and (
                            old.startswith(new) or new.startswith(old)):
                        if len(new) > len(old):
                            if target.exists():
                                extensions[target] = new
                            else:
                                imports[src][relative] = path
                    else:
                        raise AccountError('Conflicting conversation data; originals unchanged: ' + str(target))
            if not busy:
                links.append((home.name, src, dst))

    history = source / 'history.jsonl'
    histories = []
    for home in profiles:
        path = home / 'history.jsonl'
        if path.is_symlink():
            if path.resolve() != history.resolve() or not history.is_file():
                raise AccountError('Profile has a different history link: ' + str(path))
            continue
        histories.append((home, path))
    raw = history_bytes(history) if histories else b''
    if histories and history.exists():
        observe(history)
    merged, seen = raw, set(raw.splitlines())
    for home, path in histories:
        data = history_bytes(path)
        observe(path)
        for line in data.splitlines():
            if line.strip() and line not in seen:
                merged += line + b'\n'
                seen.add(line)
        if home.name not in pending:
            links.append((home.name, history, path))

    def validate():
        for path, before in observed.items():
            if signature(path) != before:
                raise AccountError('Conversation data changed during synchronization; retry.')
        for home in profiles:
            if home.name not in pending and any(dst.parent == home for _, _, dst in links) and active(home):
                raise AccountError('A Claude session started during synchronization; retry.')

    def apply():
        validate()
        for label, _, dst in links:
            if dst.exists():
                backup = backup_path(dst, label + '-context')
                if dst.is_dir():
                    shutil.copytree(dst, backup)
                else:
                    shutil.copy2(dst, backup)
        for target in extensions:
            if target.exists():
                backup = backup_path(target, 'system-context/' + str(target.parent.relative_to(source)))
                shutil.copy2(target, backup)

        validate()
        count = 0
        for src, entries in imports.items():
            src.mkdir(mode=0o700, parents=True, exist_ok=True)
            for relative, path in sorted(entries.items(), key=lambda item: len(item[0].parts)):
                target = src / relative
                if target == path:
                    continue
                if path.is_dir():
                    if target.is_symlink():
                        raise AccountError('Conversation directory changed during synchronization; retry.')
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                else:
                    # Preserve open append descriptors in profiles waiting for a safe cutover.
                    try:
                        os.link(path, target)
                    except FileExistsError:
                        raise AccountError('Conversation appeared during synchronization; originals unchanged: ' + str(target)) from None
                    count += 1
        for target, content in extensions.items():
            current = target.read_bytes()
            if current == content:
                continue
            if not content.startswith(current):
                raise AccountError('Conversation changed during synchronization; retry.')
            with target.open('ab') as out:
                out.write(content[len(current):])
                out.flush()
                os.fsync(out.fileno())
            count += 1
        if merged != raw:
            if history.exists():
                shutil.copy2(history, backup_path(history, 'system-context'))
            with history.open('ab') as out:
                out.write(merged[len(raw):])
                out.flush()
                os.fsync(out.fileno())
            history.chmod(0o600)
        elif histories and not history.exists():
            history.touch(mode=0o600)
        for label, src, dst in links:
            if active(dst.parent):
                raise AccountError('A Claude session started during synchronization; original paths retained where possible; retry.')
            if src != history:
                src.mkdir(mode=0o700, parents=True, exist_ok=True)
            if dst.exists():
                # Rename retains a recoverable original if creating the link fails.
                staging = dst.with_name('.sharing-' + dst.name)
                if staging.exists() or staging.is_symlink():
                    raise AccountError('Unfinished conversation migration: ' + str(staging))
                dst.rename(staging)
                try:
                    dst.symlink_to(src, target_is_directory=src != history)
                except OSError:
                    staging.rename(dst)
                    raise
                if staging.is_dir():
                    shutil.rmtree(staging)
                else:
                    staging.unlink()
            else:
                dst.symlink_to(src, target_is_directory=src != history)
        return {'shared_profiles': len(profiles) - len(pending),
                'pending_profiles': pending, 'imported_files': count}

    return apply

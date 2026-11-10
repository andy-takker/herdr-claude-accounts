"""Reversible shell integration, preserving the user's existing Claude wrapper."""
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

from accounts import AccountError
from launcher import upstream

BEGIN = '# BEGIN Claude Accounts\n'
END = '# END Claude Accounts\n'
FISH_MARK = '# Managed by Claude Accounts\n'


def read_regular(path):
    if path.is_symlink():
        raise AccountError('Refusing to replace symbolic-link configuration: ' + str(path))
    return path.read_text() if path.exists() else ''


def write_atomic(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.claude-accounts-')
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def without_block(text):
    if not text.count(BEGIN) and not text.count(END):
        return text
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(BEGIN) > text.index(END):
        raise AccountError('Incomplete Claude Accounts shell block; edit it manually before continuing.')
    start = text.index(BEGIN)
    stop = text.index(END) + len(END)
    return text[:start] + text[stop:]


def config_paths(home):
    home = Path(home or Path.home())
    return home / '.zshrc', home / '.config/fish/conf.d/zzz-herdr-claude-accounts.fish'


def install(store, binary=None, home=None):
    rc, fish = config_paths(home)
    old = read_regular(rc)
    base = without_block(old)
    existing_fish = read_regular(fish)
    if existing_fish and not existing_fish.startswith(FISH_MARK):
        raise AccountError('Fish integration path already contains another file; left unchanged.')
    if re.search(r'^\s*(?:alias\s+claude[= ]|(?:function\s+)?claude\s*\(\)|function\s+claude\b)', base, re.M):
        raise AccountError('A zsh alias/function named claude would bypass routing; resolve it first.')
    fish_home = fish.parent.parent
    if (fish_home / 'functions/claude.fish').exists():
        raise AccountError('A fish function named claude would bypass routing; resolve it first.')
    fish_config = read_regular(fish_home / 'config.fish')
    if re.search(r'^\s*(?:function\s+claude\b|alias\s+claude\b)', fish_config, re.M):
        raise AccountError('A fish alias/function named claude would bypass routing; resolve it first.')
    resolved = str(Path(binary).absolute()) if binary else upstream(store)
    if Path(resolved).resolve() == (store.root / 'bin/claude').resolve():
        raise AccountError('Choose the original Claude executable, not this launcher.')
    store.configure_binary(resolved)
    store.enable_shared_settings(home)
    bindir = store.root / 'bin'
    if bindir.is_symlink():
        raise AccountError('Launcher directory must not be a symbolic link.')
    bindir.mkdir(mode=0o700, exist_ok=True)
    repo = str(Path(__file__).resolve().parent)
    prefix = '#!' + sys.executable + '\nimport sys\nsys.path.insert(0, ' + repr(repo) + ')\n'
    launch_code = prefix + 'from accounts import Store\nfrom launcher import main\n' + (
        'sys.exit(main(Store(' + repr(str(store.root)) + ')))\n'
    )
    manager_code = prefix + 'from manager import main\n' + (
        'sys.exit(main(["--root", ' + repr(str(store.root)) + '] + sys.argv[1:]))\n'
    )
    write_atomic(bindir / 'claude', launch_code, 0o700)
    write_atomic(bindir / 'claude-accounts', manager_code, 0o700)
    backup = store.root / 'zshrc.backup'
    if rc.exists() and not backup.exists():
        write_atomic(backup, old)
    block = BEGIN + 'export PATH=' + shlex.quote(str(bindir)) + ':"$PATH"\n' + END
    # This user's zsh starts fish; PATH must be configured before that handoff.
    handoff = re.search(r'^\s*(?:exec\s+)?fish\s*$', base, re.M)
    if handoff:
        new = base[:handoff.start()] + block + base[handoff.start():]
    else:
        new = base + ('' if not base or base.endswith('\n') else '\n') + block
    write_atomic(rc, new, rc.stat().st_mode & 0o777 if rc.exists() else 0o600)
    fish_text = FISH_MARK + 'fish_add_path --global --path --prepend --move ' + shlex.quote(str(bindir)) + '\n'
    write_atomic(fish, fish_text)
    return {'bin': str(bindir), 'upstream': resolved, 'zsh_config': str(rc),
            'fish_config': str(fish), 'next': 'Open a new terminal, then run claude-accounts ui.'}


def uninstall(store, home=None):
    rc, fish = config_paths(home)
    old = read_regular(rc)
    fish_text = read_regular(fish)
    if fish_text and not fish_text.startswith(FISH_MARK):
        raise AccountError('Fish integration was replaced by another file; left unchanged.')
    if rc.exists():
        write_atomic(rc, without_block(old), rc.stat().st_mode & 0o777)
    if fish_text:
        fish.unlink()
    return {'uninstalled': True, 'accounts_preserved': str(store.root),
            'next': 'Open a fresh terminal; existing shells retain their current PATH.'}

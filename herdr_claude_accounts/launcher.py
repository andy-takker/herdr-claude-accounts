"""Resolve selection once, then replace this process with the original Claude."""
import os
from pathlib import Path
import shutil
import sys

from herdr_claude_accounts.accounts import AccountError, Store
from herdr_claude_accounts.claude_cli import managed_environment, system_environment


def upstream(store, state=None):
    state = state or store.read()
    value = state['binary'] or shutil.which('claude')
    if not value:
        raise AccountError('Claude is not installed or not in PATH.')
    path = Path(value).absolute()
    if path.resolve() in ((store.root / 'bin/claude').resolve(), Path(__file__).resolve()):
        raise AccountError('Recursive Claude launcher; configure the original executable.')
    if not path.is_file() or not os.access(path, os.X_OK):
        raise AccountError('The configured Claude executable is unavailable.')
    return str(path)


def launch(store, args, account=None):
    if account is not None or not os.environ.get('CLAUDE_CONFIG_DIR', '').strip():
        store.sync_settings()
    state = store.read()
    binary = upstream(store, state)
    env = dict(os.environ)
    if account is not None:
        env = system_environment(env) if account == 'system' else managed_environment(store.profile(account), env)
    elif not env.get('CLAUDE_CONFIG_DIR', '').strip() and state['selected'] is not None:
        env = managed_environment(store.profile(state['selected']), env)
    os.execve(binary, [binary] + args, env)


def main(store=None):
    try:
        launch(store or Store(), sys.argv[1:])
    except (AccountError, OSError) as error:
        message = str(error) if isinstance(error, AccountError) else 'Could not execute Claude.'
        print('Claude Accounts: ' + message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

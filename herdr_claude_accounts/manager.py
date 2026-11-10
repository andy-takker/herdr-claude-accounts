#!/usr/bin/env python3
"""Standalone CLI; Herdr opens the same manager in an overlay."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from herdr_claude_accounts.accounts import AccountError, Store
from herdr_claude_accounts.claude_cli import Claude, termination_signals
from herdr_claude_accounts.launcher import upstream

PLUGIN_ID = 'local.claude-accounts'


def client(store):
    return Claude(upstream(store))


def refresh_account(store, claude, account, local=True):
    from herdr_claude_accounts.status_cache import refresh
    return refresh(store, claude, account, local)


def check_account(store, claude, account_id):
    from herdr_claude_accounts.status_cache import check
    return check(store, claude, account_id)


def login_account(store, claude, account_id, browser=False, cancel=None):
    if cancel is not None and cancel.is_set():
        return 130, {'state': 'cancelled'}
    store.sync_settings()
    profile = store.profile(account_id)
    from herdr_claude_accounts.status_cache import invalidate_login
    from herdr_claude_accounts.usage import invalidate
    invalidate_login(store, account_id)
    invalidate(store, account_id)
    status = None
    try:
        code = claude.login(profile, browser=True, cancel=cancel) if browser else claude.login(profile)
        if browser and code == 130:
            return code, {'state': 'cancelled'}
        if cancel is not None and cancel.is_set():
            return 130, {'state': 'cancelled'}
        status = claude.auth(profile)
        return code, status
    finally:
        invalidate_login(store, account_id, status)
        invalidate(store, account_id)


def parser():
    p = argparse.ArgumentParser(description='Independent Claude accounts for Herdr and terminals.')
    p.add_argument('--root', type=Path, help='Use a separate data directory (default: ~/.local/share/herdr-claude-accounts).')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='List accounts and selected account as JSON.')
    add = sub.add_parser('add', help='Create a private profile and sign in using the browser.')
    add.add_argument('label')
    add.add_argument('--no-login', action='store_true', help='Create the profile now; sign in later.')
    select = sub.add_parser('select', help='Select an account ID or system for future launches.')
    select.add_argument('account')
    remove = sub.add_parser('remove', help='Remove an account from the manager; select System default and retain profile data.')
    remove.add_argument('account')
    sub.add_parser('removed', help='List retained profiles that can be restored.')
    restore = sub.add_parser('restore', help='Restore a removed profile without changing the selection.')
    restore.add_argument('account')
    launch = sub.add_parser('launch', help='Launch a specific profile without changing the global selection.')
    launch.add_argument('account')
    launch.add_argument('args', nargs=argparse.REMAINDER)
    prepare = sub.add_parser('prepare-login', help='Create a cancellable browser-login ticket.')
    prepare.add_argument('account')
    cancel = sub.add_parser('cancel-login', help='Cancel only the browser login identified by its ticket.')
    cancel.add_argument('attempt')
    for name, help_text in [('login', 'Sign in again to a profile.'),
                            ('check', 'Verify service access with one short model request (uses quota).')]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument('account')
        if name == 'login':
            command.add_argument('--browser', action='store_true', help='Browser login with JSON output and a five-minute timeout.')
            command.add_argument('--attempt', help='Ticket returned by prepare-login.')
    status = sub.add_parser('status', help='Refresh local sign-in status and sessions for all profiles.')
    status.add_argument('--usage', action='store_true', help='Include quota data cached for five minutes (no model requests).')
    usage = sub.add_parser('refresh-usage', help='Refresh only this account quota, bypassing the five-minute cache.')
    usage.add_argument('account', help='Account ID or system.')
    sub.add_parser('sessions', help='List native active sessions for all profiles.')
    sub.add_parser('share-history', help='Merge existing conversation context with the system profile, back up originals and enable sharing.')
    sub.add_parser('sync-settings', help='Share user settings from ~/.claude; preserve each account login.')
    sub.add_parser('ui', help='Open the interactive account manager.')
    sub.add_parser('open-ui', help='Open the manager in a Herdr overlay.')
    install = sub.add_parser('install-shell', help='Add reversible launcher routing to zsh.')
    install.add_argument('--binary', type=Path, help='Original Claude executable, including any proxy wrapper.')
    sub.add_parser('uninstall-shell', help='Remove only our zsh PATH block; keep accounts.')
    return p


def run(args):
    store = Store(args.root)
    if args.command in ('prepare-login', 'cancel-login'):
        from herdr_claude_accounts.login_attempt import LoginAttempt
        return (LoginAttempt.prepare(store, args.account) if args.command == 'prepare-login'
                else LoginAttempt(store, args.attempt).cancel())
    if args.command == 'list':
        return store.read()
    if args.command == 'refresh-usage':
        from herdr_claude_accounts.usage import cached_usage
        account_id = None if args.account == 'system' else args.account
        profile = store.profile(account_id) if account_id else None
        claude = client(store)
        return cached_usage(store, claude, account_id, claude.auth(profile), force=True)
    if args.command == 'share-history':
        return store.enable_shared_conversations()
    if args.command == 'sync-settings':
        return store.enable_shared_settings()
    if args.command == 'select':
        store.select(None if args.account == 'system' else args.account)
        return {'selected': store.read()['selected']}
    if args.command == 'remove':
        return store.remove(args.account)
    if args.command == 'removed':
        return store.removed()
    if args.command == 'restore':
        return store.restore(args.account)
    if args.command == 'launch':
        from herdr_claude_accounts.launcher import launch
        launch(store, args.args, account=args.account)
        return None
    if args.command == 'add':
        account = store.create(args.label)
        if not args.no_login and not account.get('warning'):
            print('Signing in to ' + account['label'] + ' (' + account['id'] + ')', file=sys.stderr)
            code, _ = login_account(store, client(store), account['id'])
            if code != 0:
                raise AccountError('Sign-in did not finish. Profile retained: ' + account['id'])
        return account
    if args.command == 'login':
        if args.attempt:
            from herdr_claude_accounts.login_attempt import LoginAttempt
            if not args.browser:
                raise AccountError('A login ticket requires --browser.')
            with LoginAttempt(store, args.attempt).running(args.account) as attempt:
                code, status = login_account(store, client(store), args.account, browser=True, cancel=attempt)
        else:
            code, status = login_account(store, client(store), args.account, browser=args.browser)
        if status.get('state') == 'cancelled':
            return status
        if code != 0:
            raise AccountError('Sign-in did not finish; the profile was retained.')
        if args.browser and status['state'] != 'saved':
            raise AccountError('Sign-in could not be confirmed; profile retained. Refresh or retry sign-in.')
        return status
    if args.command == 'check':
        print('Checking access with one short model request; this uses account quota.', file=sys.stderr)
        return check_account(store, client(store), args.account)
    if args.command in ('status', 'sessions'):
        state = store.read()
        claude = client(store)
        system = refresh_account(store, claude, {'id': None, 'label': 'System default'},
                                 local=args.command == 'status')
        accounts = [
            refresh_account(store, claude, a, local=args.command == 'status') for a in state['accounts']
        ]
        if args.command == 'status' and args.usage:
            from herdr_claude_accounts.usage import cached_usage
            for row in [system] + accounts:
                row['usage'] = cached_usage(store, claude, row['id'], row['auth'])
        return {'selected': store.read()['selected'], 'system': system, 'accounts': accounts}
    if args.command == 'ui':
        from herdr_claude_accounts.ui import show
        show(store)
        return None
    if args.command == 'open-ui':
        if os.environ.get('HERDR_ENV') != '1':
            raise AccountError('Run open-ui inside Herdr, or use claude-accounts ui in this terminal.')
        result = subprocess.run([os.environ.get('HERDR_BIN_PATH', 'herdr'), 'plugin', 'pane', 'open',
                                 '--plugin', PLUGIN_ID, '--entrypoint', 'accounts'])
        if result.returncode:
            raise AccountError('Herdr could not open the account manager.')
        return None
    if args.command in ('install-shell', 'uninstall-shell'):
        from herdr_claude_accounts.shell_install import install, uninstall
        if args.command == 'install-shell':
            return install(store, args.binary)
        return uninstall(store)


def main(argv=None):
    try:
        with termination_signals():
            value = run(parser().parse_args(argv))
        if value is not None:
            print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        return 130
    except (AccountError, OSError) as error:
        message = str(error) if isinstance(error, AccountError) else 'Local operation failed; check file permissions and executable paths.'
        print('Claude Accounts: ' + message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

"""A small terminal UI, shared by Herdr overlays and external terminals."""
from concurrent.futures import ThreadPoolExecutor
import curses
import os
import threading
import time
import sys
import termios
import signal
import json
from pathlib import Path
import shlex
import subprocess

from herdr_claude_accounts.accounts import AccountError
from herdr_claude_accounts.claude_cli import Claude
from herdr_claude_accounts.launcher import upstream
from herdr_claude_accounts.manager import check_account, refresh_account, login_account
from herdr_claude_accounts.usage import cached_usage

AUTH_LABELS = {'provider': 'API/provider sign-in', 'saved': 'sign-in saved (local)', 'verified': 'verified online',
               'needs_login': 'SIGN IN REQUIRED', 'limited': 'usage limit',
               'access_denied': 'access/billing restriction', 'network_error': 'network error',
               'unknown': 'not verified'}


def clean(value):
    return ''.join(c if c.isprintable() else '?' for c in str(value))


def stamp(value):
    return time.strftime('%H:%M %d.%m.%Y', time.localtime(value)) if value else 'not checked'


def headroom(row):
    windows = row.get('usage', {}).get('windows', [])
    return round(min(100 - w['used_percent'] for w in windows)) if windows else None


def sessions_of(row):
    sessions = row.get('sessions', {})
    return sessions.get('sessions', []) if sessions.get('available') else []


def short_path(path):
    home = str(Path.home())
    path = str(path or '')
    return '~' + path[len(home):] if path == home or path.startswith(home + '/') else path


def render_lines(rows, selected, cursor, session_cursor=None, usage_colors=None):
    """Accounts list on top, the highlighted account below it, its sessions last."""
    lines = ['Claude Accounts', 'New sessions use: ' + next(
        (clean(a['label']) for a in rows if a['id'] == selected),
        'System default' if selected is None else 'unavailable profile'), '', 'ACCOUNTS']
    for i, row in enumerate(rows):
        state = row.get('auth', {}).get('state')
        room = headroom(row)
        sessions = row.get('sessions', {})
        count = len(sessions.get('sessions', [])) if sessions.get('available') else None
        summary = 'SIGN IN REQUIRED' if state == 'needs_login' else ' · '.join(filter(None, [
            '' if room is None else '{}% headroom'.format(room),
            'sessions unavailable' if count is None else '{} session{}'.format(count, '' if count == 1 else 's')]))
        bar = '' if room is None else '[{:10}]  '.format('#' * round((100 - room) / 10))
        if room is not None and usage_colors is not None:
            usage_colors[len(lines)] = (100 - room, 29)
        lines.append('{}{} {:22.22}  {}{}'.format('> ' if i == cursor else '  ', '*' if row['id'] == selected else ' ',
                                                 clean(row['label']), bar, summary))
    if not rows:
        return lines
    row = rows[cursor]
    auth = row.get('auth', {})
    lines += ['', '─' * 60, clean(row['label']).upper() + ('   (used for new sessions)' if row['id'] == selected else '')]
    identity = (' | ' + clean(auth['email'])) if auth.get('email') else ''
    lines.append('  ' + AUTH_LABELS.get(auth.get('state'), 'not checked') + identity
                 + ('   checked ' + stamp(auth['checked_at']) if auth.get('checked_at') else ''))
    online = row.get('online', {})
    if online.get('state'):
        lines.append('  Last online check: ' + AUTH_LABELS.get(online['state'], 'unknown') + ' at ' + stamp(online.get('checked_at')))
    usage = row.get('usage', {})
    windows = usage.get('windows', [])
    if windows:
        stale = (usage.get('state') == 'stale' or time.time() - usage.get('checked_at', 0) > 330
                 or any(w.get('resets_at') and w['resets_at'] <= time.time() for w in windows))
        lines.append('  Usage · ' + stamp(usage.get('checked_at')) + (' (stale)' if stale else ''))
        for window in windows:
            percent = window['used_percent']
            if usage_colors is not None:
                usage_colors[len(lines)] = (percent, 3)
            reset = 'resets ' + stamp(window['resets_at']) if window.get('resets_at') else ''
            lines.append('  [{:10}]  {:18}  {:>4}% free  {}'.format(
                '#' * round(percent / 10), clean(window['label']), format(100 - percent, '.0f'), reset))
    else:
        lines.append('  Usage unavailable: ' + clean(usage.get('reason', 'not checked')))
    sessions = row.get('sessions', {})
    if not sessions.get('available'):
        lines.append('  Running sessions: unavailable (not zero)')
    else:
        active = sessions.get('sessions', [])
        lines.append('  Running sessions: {}{}'.format(len(active), '' if session_cursor is not None or not active else '   (Tab to act on one)'))
        for i, session in enumerate(active):
            lines.append('  {} {}  {}'.format('>' if i == session_cursor else ' ', clean(short_path(session.get('cwd')) or session.get('id', '?')),
                                              ' · '.join(clean(session.get(k)) for k in ('state', 'kind', 'id') if session.get(k))))
    if row.get('error'):
        lines.append('  ' + clean(row['error']))
    return lines


def load_rows(store, cancel, local=True):
    state = store.read()
    cache = store.cached()
    claude = Claude(upstream(store, state), cancel=cancel)
    rows = []
    for account in [{'id': None, 'label': 'System default'}] + state['accounts']:
        if cancel.is_set():
            break
        cached = cache.get(account['id'], {})
        row = dict(account, auth=cached.get('auth', {}), online=cached.get('online', {}))
        try:
            row.update(refresh_account(store, claude, account, local))
            if not cancel.is_set():
                row['usage'] = cached_usage(store, claude, account['id'], row['auth'])
        except (AccountError, OSError):
            row.update(error='Profile or authentication configuration unavailable.',
                       sessions={'available': False, 'sessions': []})
        rows.append(row)
    return rows


def refresh_usage(store, cancel, account_id):
    claude = Claude(upstream(store), cancel=cancel)
    profile = store.profile(account_id) if account_id else None
    return cached_usage(store, claude, account_id, claude.auth(profile), force=True)


def sign_in(store, cancel, account_id):
    code, auth = login_account(store, Claude(upstream(store), cancel=cancel),
                              account_id, browser=True, cancel=cancel)
    if code == 130:
        return 'Sign-in cancelled; profile retained.'
    if code == 0 and auth.get('state') == 'saved':
        return 'Sign-in saved. Press Enter to select this account.'
    return 'Sign-in not confirmed; profile retained. Press l to try again.'


def launch_command(store, account_id, args=()):
    return [sys.executable, str(Path(__file__).with_name('__main__.py').resolve()),
            '--root', str(store.root), 'launch', account_id or 'system'] + (['--'] + list(args) if args else [])


def launch_tab(store, account_id, args=(), cwd=None):
    workspace = os.environ.get('HERDR_WORKSPACE_ID')
    if os.environ.get('HERDR_ENV') != '1' or not workspace:
        raise AccountError('Open this menu inside a Herdr workspace to launch a new tab.')
    binary = os.environ.get('HERDR_BIN_PATH', 'herdr')
    try:
        created = subprocess.run([binary, 'tab', 'create', '--workspace', workspace,
                                  '--cwd', cwd or os.getcwd(), '--label', 'Claude', '--focus'],
                                 capture_output=True, text=True, timeout=10)
        if created.returncode:
            raise AccountError('Could not create a Herdr tab.')
        pane = json.loads(created.stdout)['result']['root_pane']['pane_id']
        if not isinstance(pane, str) or not pane:
            raise ValueError()
    except (ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise AccountError('Herdr did not confirm the new tab. Check its tabs before retrying.') from None
    try:
        started = subprocess.run([binary, 'pane', 'run', pane, shlex.join(launch_command(store, account_id, args))],
                                 capture_output=True, timeout=10)
        if started.returncode:
            raise AccountError('Tab created, but Claude could not be started. Check the new tab.')
    except subprocess.TimeoutExpired:
        raise AccountError('Tab created; launch was not confirmed. Check the new tab before retrying.') from None
    return 'Launch queued in a new tab for this account.'


def usage_band(percent):
    return 1 if percent < 70 else 2 if percent < 80 else 3 if percent < 90 else 4


def init_colors():
    if not curses.has_colors():
        return False
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    colors = (33, 220, 208, 196) if curses.COLORS >= 256 else (
        curses.COLOR_BLUE, curses.COLOR_YELLOW, curses.COLOR_YELLOW, curses.COLOR_RED)
    for pair, color in enumerate(colors, 1):
        curses.init_pair(pair, color, background)
    return True


def put(screen, y, text):
    height, width = screen.getmaxyx()
    if 0 <= y < height and width > 1:
        try:
            screen.addnstr(y, 0, clean(text), width - 1)
        except curses.error:
            pass


def ask(screen, prompt):
    height, width = screen.getmaxyx()
    screen.move(max(0, height - 2), 0)
    screen.clrtoeol()
    put(screen, height - 2, prompt)
    screen.refresh()
    screen.timeout(-1)
    curses.echo()
    try:
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        return screen.getstr(max(0, height - 1), 0, max(1, min(width - 1, 80))).decode('utf-8').strip()
    except (UnicodeError, KeyboardInterrupt):
        return ''
    finally:
        curses.noecho()
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.timeout(200)


def run(screen, store):
    screen.keypad(True)
    screen.timeout(200)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    colored = init_colors()
    rows, cursor, offset, message = [], 0, 0, 'Loading profiles…'
    session_cursor, removed, deleted = None, False, []
    focus = None
    follow_cursor = False
    pool = ThreadPoolExecutor(max_workers=1)
    cancel = threading.Event()
    future = pool.submit(load_rows, store, cancel)
    job, job_id = 'refresh', None
    next_refresh = time.monotonic() + 15

    def stop_job():
        cancel.set()
        if future:
            try:
                future.result()
            except (AccountError, OSError):
                pass

    def add_and_login(label, event):
        account = store.create(label)
        return (account.get('warning') or sign_in(store, event, account['id'])), account['id']

    try:
        while True:
            if future and future.done():
                reload_rows = job not in ('refresh', 'usage', 'launch')
                try:
                    result = future.result()
                    if job == 'refresh':
                        rows = result
                        if not removed:
                            cursor = next((i for i, r in enumerate(rows) if r['id'] == focus), 0)
                        if message == 'Loading profiles…':
                            message = 'Ready. Selection applies to new launches.'
                    elif job == 'usage':
                        rows = [dict(r, usage=result) if r['id'] == job_id else r for r in rows]
                        message = 'Usage refreshed.' if result.get('state') == 'available' else 'Usage unavailable or stale; previous data retained.'
                    elif job == 'check':
                        message = 'Online check: ' + AUTH_LABELS.get(result['state'], 'unknown')
                    elif job == 'add':
                        message, focus = result
                    elif job in ('login', 'launch'):
                        message = result
                    elif job == 'remove':
                        message, focus = 'Account removed. System default selected; profile retained.', None
                    elif job == 'restore':
                        message = 'Account restored. Selection unchanged.'
                    elif job == 'select':
                        message = 'Selected for new launches. Running sessions are unchanged.'
                except (AccountError, OSError) as error:
                    message = str(error) if isinstance(error, AccountError) else 'Local operation failed.'
                future = None
                next_refresh = time.monotonic() + 15
                if reload_rows:
                    # Publish mutations before polling, so immediate keys target the new row.
                    previous = {r['id']: r for r in rows}
                    accounts = [{'id': None, 'label': 'System default'}] + store.read()['accounts']
                    rows = [dict(previous.get(a['id'], {}), **a) for a in accounts]
                    if not removed:
                        cursor = next((i for i, r in enumerate(rows) if r['id'] == focus), 0)
                    deleted = [r['account'] for r in store.removed()] if removed else []
                    cancel = threading.Event()
                    future = pool.submit(load_rows, store, cancel)
                    job = 'refresh'
                    follow_cursor = True
            state = store.read()
            items = deleted if removed else rows
            cursor = max(0, min(cursor, len(items) - 1))
            height, width = screen.getmaxyx()
            screen.erase()
            usage_colors = {}
            if removed:
                lines = ['Claude Accounts — Removed accounts', 'Retained profiles; restore keeps the existing sign-in.', '']
                lines += [('> ' if i == cursor else '  ') + clean(r['label']) for i, r in enumerate(items)]
                if not items:
                    lines.append('No removed accounts.')
            else:
                active = sessions_of(rows[cursor]) if rows else []
                if session_cursor is not None and not active:
                    session_cursor = None
                elif session_cursor is not None:
                    session_cursor = min(session_cursor, len(active) - 1)
                lines = render_lines(rows, state['selected'], cursor, session_cursor, usage_colors)
            visible = max(1, height - 6)
            if follow_cursor:
                target = next((i for i, line in enumerate(lines) if line.startswith('> ')), 0)
                if target < offset or target >= offset + visible:
                    offset = target
                follow_cursor = False
            offset = max(0, min(offset, max(0, len(lines) - visible)))
            for y, line in enumerate(lines[offset:offset + visible]):
                put(screen, y, line)
                if colored and y + offset in usage_colors:
                    percent, column = usage_colors[y + offset]
                    attr = curses.color_pair(usage_band(percent))
                    if 80 <= percent < 90 and curses.COLORS < 256:
                        attr |= curses.A_BOLD
                    try:
                        if width > column + 11:
                            screen.addnstr(y, column, line[column:column + 10], 10, attr)
                    except curses.error:
                        pass
            if removed:
                put(screen, height - 5, '↑/↓ account   Enter restore   b/Esc back   q quit')
            elif session_cursor is not None:
                put(screen, height - 5, 'Session  ↑/↓ session   Enter continue with another account')
                put(screen, height - 4, '         Tab/Esc back to accounts')
                put(screen, height - 3, '')
            else:
                put(screen, height - 5, 'List     ↑↓ account  a add  o removed  s system  r refresh  q quit')
                put(screen, height - 4, 'Account  Enter default  n new  N continue latest  Tab sessions  u usage')
                put(screen, height - 3, '         l sign in  c cancel sign-in  v check (quota)  d remove')
            put(screen, height - 2, message + (' [' + job + '…]' if future else ''))
            if os.environ.get('CLAUDE_CONFIG_DIR'):
                put(screen, height - 1, 'Explicit CLAUDE_CONFIG_DIR overrides ordinary launches; n pins this account.')
            screen.refresh()
            key = screen.getch()
            if key == ord('q') or (key == 27 and not removed and session_cursor is None):
                break
            if session_cursor is not None and key in (27, 9, ord(' ')):
                session_cursor = None
            elif session_cursor is not None and key in (curses.KEY_UP, ord('k'), curses.KEY_DOWN, ord('j')):
                session_cursor = max(0, min(len(sessions_of(rows[cursor])) - 1,
                                            session_cursor + (-1 if key in (curses.KEY_UP, ord('k')) else 1)))
            elif session_cursor is not None and key != -1 and key not in (10, 13, curses.KEY_ENTER, curses.KEY_NPAGE, curses.KEY_PPAGE):
                pass  # account-level keys are inactive while a session is focused
            elif session_cursor is not None and key in (10, 13, curses.KEY_ENTER) and not (future and job != 'refresh'):
                session = sessions_of(rows[cursor])[session_cursor]
                others = [r for r in rows if r['id'] != rows[cursor]['id'] and r.get('auth', {}).get('state') != 'needs_login']
                resume = session.get('sessionId')
                if not resume or not others:
                    message = 'This session cannot be resumed elsewhere (no session id or no other signed-in account).'
                    continue
                choice = ask(screen, 'Exit it first. Resume with: ' + '  '.join(
                    '{} {}'.format(i + 1, clean(r['label'])) for i, r in enumerate(others)) + '  (number, empty cancels):')
                if not choice.isdigit() or not 1 <= int(choice) <= len(others):
                    message = 'Resume cancelled.'
                    continue
                target = others[int(choice) - 1]['id']
                stop_job()
                cancel = threading.Event()
                try:
                    if os.environ.get('HERDR_ENV') == '1':
                        job, future = 'launch', pool.submit(launch_tab, store, target, ['--resume', resume], session.get('cwd'))
                    else:
                        curses.def_prog_mode()
                        curses.endwin()
                        try:
                            subprocess.call(launch_command(store, target, ['--resume', resume]), cwd=session.get('cwd') or None)
                            message = 'Claude exited. Returned to account manager.'
                        finally:
                            curses.reset_prog_mode()
                            screen.clear()
                except (AccountError, OSError) as error:
                    message = str(error) if isinstance(error, AccountError) else 'Local operation failed.'
            elif key in (curses.KEY_UP, ord('k'), curses.KEY_DOWN, ord('j')):
                cursor = max(0, min(len(items) - 1, cursor + (-1 if key in (curses.KEY_UP, ord('k')) else 1)))
                if not removed and rows:
                    focus = rows[cursor]['id']
                follow_cursor = True
            elif key == curses.KEY_NPAGE:
                offset += visible
            elif key == curses.KEY_PPAGE:
                offset -= visible
            elif key == ord('c') and future and job in ('login', 'add'):
                cancel.set()
                message = 'Cancelling sign-in…'
            elif future and job != 'refresh':
                continue
            elif key in (ord(' '), 9) and not removed and rows:
                if sessions_of(rows[cursor]):
                    session_cursor = 0
                else:
                    message = 'No running sessions for this account.'
            elif key == ord('o') or (removed and key in (ord('b'), 27)):
                stop_job()
                future = None
                removed = key == ord('o')
                deleted = [r['account'] for r in store.removed()] if removed else []
                cursor, offset, follow_cursor = 0, 0, True
                next_refresh = time.monotonic()
            elif key in (ord('a'), ord('l'), ord('v'), ord('r'), ord('s'), ord('d'), ord('u'), ord('n'), ord('N'), 10, 13, curses.KEY_ENTER):
                stop_job()
                future = None
                cancel = threading.Event()
                account = items[cursor] if items else None
                account_id = account['id'] if account else None
                job_id = account_id
                try:
                    if removed:
                        if key in (10, 13, curses.KEY_ENTER) and account:
                            job, future = 'restore', pool.submit(store.restore, account_id)
                        elif key == ord('r'):
                            deleted = [r['account'] for r in store.removed()]
                    elif key == ord('a'):
                        label = ask(screen, 'New account name (empty cancels):')
                        if label:
                            message = 'Finish sign-in in your browser; c cancels.'
                            job, future = 'add', pool.submit(add_and_login, label, cancel)
                    elif key == ord('l') and account_id:
                        message = 'Finish sign-in in your browser; c cancels.'
                        job, future = 'login', pool.submit(sign_in, store, cancel, account_id)
                    elif key == ord('d') and account_id:
                        if ask(screen, 'Remove ' + clean(account['label']) + '? System default will be selected. Type y:').lower() == 'y':
                            job, future = 'remove', pool.submit(store.remove, account_id)
                    elif key == ord('v') and account_id:
                        if ask(screen, 'One short model request uses quota. Type y to check:').lower() == 'y':
                            job, future = 'check', pool.submit(check_account, store, Claude(upstream(store), cancel=cancel), account_id)
                    elif key == ord('u') and account:
                        job, future = 'usage', pool.submit(refresh_usage, store, cancel, account_id)
                    elif key == ord('s') or (key in (10, 13, curses.KEY_ENTER) and account):
                        job, future = 'select', pool.submit(store.select, None if key == ord('s') else account_id)
                    elif key in (ord('n'), ord('N')) and account:
                        args = ['--continue'] if key == ord('N') else []
                        if os.environ.get('HERDR_ENV') == '1':
                            job, future = 'launch', pool.submit(launch_tab, store, account_id, args)
                        else:
                            curses.def_prog_mode()
                            curses.endwin()
                            try:
                                subprocess.call(launch_command(store, account_id, args))
                                message = 'Claude exited. Returned to account manager.'
                            finally:
                                curses.reset_prog_mode()
                                screen.clear()
                    elif key in (ord('l'), ord('d'), ord('v')) and account:
                        message = 'System default is managed by Claude; use a to add an independent account.'
                except (AccountError, OSError) as error:
                    message = str(error) if isinstance(error, AccountError) else 'Local operation failed.'
                if future is None and not removed:
                    job, future = 'refresh', pool.submit(load_rows, store, cancel)
            if future is None and not removed and time.monotonic() >= next_refresh:
                cancel = threading.Event()
                job, future = 'refresh', pool.submit(load_rows, store, cancel)
    finally:
        cancel.set()
        pool.shutdown(wait=True)


def show(store):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise AccountError('An interactive terminal is required; try claude-accounts list.')
    saved = termios.tcgetattr(sys.stdin.fileno())
    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    handlers = {sig: signal.signal(sig, terminate) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        curses.wrapper(run, store)
    except curses.error:
        raise AccountError('A usable interactive terminal is required; try claude-accounts list.') from None
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, saved)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)

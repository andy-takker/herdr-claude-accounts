import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
import threading
from unittest.mock import patch

from herdr_claude_accounts.accounts import Store
from herdr_claude_accounts.ui import render_lines, load_rows, launch_tab, launch_command, usage_band, sign_in

REPO = Path(__file__).resolve().parents[1]


class UiTests(unittest.TestCase):
    def test_session_failures_are_not_rendered_as_zero_and_old_account_is_marked(self):
        rows = [{'id': 'a', 'label': 'Work', 'auth': {'state': 'saved'},
                 'sessions': {'available': False, 'sessions': []}},
                {'id': 'b', 'label': 'Personal', 'sessions': {'available': True, 'sessions': [
                    {'id': 'session-1', 'state': 'working', 'kind': 'background', 'cwd': '/project'}]}}]
        folded = '\n'.join(render_lines(rows, 'a', 0))
        self.assertNotIn('/project', folded)
        text = '\n'.join(render_lines(rows, 'a', 0, expanded={'b'}))
        self.assertIn('unavailable', text)
        self.assertIn('other account', text)
        self.assertIn('working', text)
        self.assertNotIn('0 sessions', text)

    def test_terminal_controls_are_not_rendered_from_account_or_session_metadata(self):
        text = '\n'.join(render_lines([{'id': 'a', 'label': 'X\x1b[2J',
            'sessions': {'available': True, 'sessions': [{'id': 'x', 'state': '\x1b[31m', 'cwd': '\nFAKE'}]}}], None, 0, expanded={'a'}))
        self.assertNotIn('\x1b', text)
        self.assertNotIn('\nFAKE', text)

    def test_system_first_and_usage_cache_shared_across_tui_refreshes(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root); first=store.create('First'); second=store.create('Second')
            store.configure_binary('/bin/echo')
            class Client:
                binary='/bin/echo'
                calls=[]
                def __init__(self,*args,**kwargs): pass
                def auth(self,profile): return {'state':'saved','email':'system@test' if profile is None else profile.name,'orgId':'org'}
                def sessions(self,profile): return {'available':True,'sessions':[]}
                def usage(self,profile):
                    self.calls.append(profile)
                    return {'state':'available','checked_at':1000,'windows':[
                        {'label':'Weekly','used_percent':80,'resets_at':2000}]}
            with patch('herdr_claude_accounts.ui.Claude',Client), patch('herdr_claude_accounts.usage.time.time',return_value=1000):
                rows=load_rows(store,threading.Event(),True)
                load_rows(Store(root),threading.Event(),True)
            self.assertEqual([r['id'] for r in rows],[None,first['id'],second['id']])
            self.assertEqual(rows[0]['auth']['email'],'system@test')
            self.assertEqual(len(Client.calls),3)
            colors={}
            text='\n'.join(render_lines(rows,None,0,usage_colors=colors))
            self.assertIn('20% free',text)
            self.assertIn('resets',text)
            self.assertEqual(list(colors.values()),[80,80,80])

    def test_system_provider_sign_in_is_not_reported_as_unchecked(self):
        lines = render_lines([{'id': None, 'label': 'System default',
                               'auth': {'state': 'provider'}}], None, 0)
        self.assertIn('API/provider sign-in', lines[4])

    def test_color_boundaries_and_unconfirmed_login(self):
        self.assertEqual([usage_band(n) for n in (0, 69, 70, 79, 80, 89, 90, 100)],
                         [1, 1, 2, 2, 3, 3, 4, 4])
        with patch('herdr_claude_accounts.ui.upstream', return_value='/bin/echo'), patch('herdr_claude_accounts.ui.login_account', return_value=(0, {'state': 'needs_login'})):
            self.assertIn('not confirmed', sign_in(None, threading.Event(), 'account'))

    def test_launch_uses_callers_workspace_and_pinned_quoted_command(self):
        with tempfile.TemporaryDirectory(prefix='accounts space ') as root:
            store = Store(root)
            calls = [subprocess.CompletedProcess([], 0, json.dumps({'result': {'root_pane': {'pane_id': 'w7:p3'}}})),
                     subprocess.CompletedProcess([], 0)]
            with patch.dict(os.environ, {'HERDR_ENV': '1', 'HERDR_WORKSPACE_ID': 'w7', 'HERDR_BIN_PATH': '/test/herdr'}), \
                    patch('herdr_claude_accounts.ui.subprocess.run', side_effect=calls) as run:
                launch_tab(store, None)
            create, start = [call.args[0] for call in run.call_args_list]
            self.assertEqual(create[:5], ['/test/herdr', 'tab', 'create', '--workspace', 'w7'])
            self.assertEqual(start[:4], ['/test/herdr', 'pane', 'run', 'w7:p3'])
            import shlex
            self.assertEqual(shlex.split(start[4]), launch_command(store, None))

    def test_pty_add_cancel_relogin_usage_select_remove_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / 'data')
            store.configure_binary(REPO / 'tests/tui_fixture.py')
            master, slave = pty.openpty()
            self.addCleanup(os.close, master)
            self.addCleanup(os.close, slave)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
            before = termios.tcgetattr(slave)
            env = dict(os.environ, TERM='xterm-256color', HOME=tmp, TUI_FIXTURE_ROOT=tmp)
            env.pop('CLAUDE_CONFIG_DIR', None)
            proc = subprocess.Popen([sys.executable, str(REPO / 'herdr_claude_accounts/__main__.py'), '--root', str(store.root), 'ui'],
                                    stdin=slave, stdout=slave, stderr=slave, env=env)
            output = b''
            def wait_for(condition):
                nonlocal output
                deadline = time.monotonic() + 8
                while not condition() and time.monotonic() < deadline and proc.poll() is None:
                    if select.select([master], [], [], .05)[0]:
                        output += os.read(master, 65536)
                self.assertTrue(condition(), repr(output[-2000:]))
            def dead(pid):
                try:
                    os.kill(pid, 0)
                    return False
                except ProcessLookupError:
                    return True
            try:
                wait_for(lambda: b'Ready.' in output)
                self.assertNotIn(b'/fixture-session-path', output)
                os.write(master, b' ')
                wait_for(lambda: b'/fixture-session-path' in output)
                os.write(master, b'a')
                wait_for(lambda: b'New account name' in output)
                os.write(master, b'Fixture account\n')
                pid_file = root / 'login.pid'
                wait_for(pid_file.exists)
                pid = int(pid_file.read_text())
                account_id = store.read()['accounts'][0]['id']
                os.write(master, b'c')
                wait_for(lambda: dead(pid))
                wait_for(lambda: b'Sign-in cancelled' in output)
                self.assertIsNone(store.read()['selected'])
                self.assertTrue(store.profile(account_id).is_dir())
                os.write(master, b'l')
                wait_for(lambda: pid_file.read_text() != str(pid))
                (root / 'finish-login').touch()
                wait_for(lambda: b'Sign-in saved.' in output)
                wait_for(lambda: account_id in (root / 'usage-calls').read_text())
                usage_before = (root / 'usage-calls').read_text().splitlines()
                os.write(master, b'u')
                wait_for(lambda: (root / 'usage-calls').read_text().splitlines().count(account_id) == 2)
                self.assertEqual((root / 'usage-calls').read_text().splitlines().count('.claude'), usage_before.count('.claude'))
                wait_for(lambda: b'Usage refreshed.' in output)
                os.write(master, b'\n')
                wait_for(lambda: store.read()['selected'] == account_id)
                wait_for(lambda: b'Selected for new launches.' in output)
                os.write(master, b'd')
                wait_for(lambda: b'Type y:' in output)
                os.write(master, b'y\n')
                wait_for(lambda: not store.read()['accounts'])
                wait_for(lambda: b'Account removed.' in output)
                self.assertIsNone(store.read()['selected'])
                os.write(master, b'o')
                wait_for(lambda: b'Removed accounts' in output)
                os.write(master, b'\n')
                wait_for(lambda: len(store.read()['accounts']) == 1)
                wait_for(lambda: b'Selection unchanged.' in output)
                self.assertEqual(store.read()['accounts'][0]['id'], account_id)
                self.assertTrue((store.profile(account_id) / 'signed-in').exists())
                self.assertIsNone(store.read()['selected'])
                self.assertEqual(store.removed(), [])
                os.write(master, b'bq')
                wait_for(lambda: proc.poll() is not None)
                self.assertEqual(proc.returncode, 0, output)
                after = termios.tcgetattr(slave)
                for settings in (before, after):
                    settings[3] &= ~getattr(termios, 'PENDIN', 0)
                self.assertEqual(after, before)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

    def test_ui_opens_quits_and_restores_terminal(self):
        with tempfile.TemporaryDirectory() as root:
            Store(root).configure_binary('/bin/echo')
            master, slave = pty.openpty()
            self.addCleanup(os.close, master)
            self.addCleanup(os.close, slave)
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 28, 100, 0, 0))
            before = termios.tcgetattr(slave)
            proc = subprocess.Popen([sys.executable, str(REPO / 'herdr_claude_accounts/__main__.py'), '--root', root, 'ui'],
                                    stdin=slave, stdout=slave, stderr=slave,
                                    env=dict(os.environ, TERM='xterm-256color'))
            output = b''
            try:
                deadline = time.monotonic() + 5
                while b'Claude Accounts' not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 65536)
                self.assertIn(b'Claude Accounts', output)
                os.write(master, b'q')
                # A real terminal keeps consuming output while curses restores its mode.
                deadline = time.monotonic() + 5
                while proc.poll() is None and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        output += os.read(master, 65536)
                self.assertEqual(proc.poll(), 0, output)
                after = termios.tcgetattr(slave)
                # Darwin sets PENDIN when restoring canonical input, even with tty.setcbreak alone.
                after[3] &= ~getattr(termios, 'PENDIN', 0)
                before[3] &= ~getattr(termios, 'PENDIN', 0)
                self.assertEqual(after, before)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()


if __name__ == '__main__':
    unittest.main()

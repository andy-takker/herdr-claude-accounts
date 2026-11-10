import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

from accounts import Store
from claude_cli import Claude
from manager import refresh_account

REPO = Path(__file__).resolve().parents[1]


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data')
        self.account = self.store.create('Work')
        self.profile = self.store.profile(self.account['id'])
        self.fake = self.root / 'claude'
        shutil.copyfile(REPO / 'tests/fake_claude.py', self.fake)
        self.fake.chmod(0o700)
        self.store.configure_binary(self.fake)
        self.fixture = {'exit': 0, 'auth': {'loggedIn': True, 'authMethod': 'claude.ai',
                                          'email': 'new@example.com'}}
        self.write_fixture()

    def write_fixture(self):
        (self.profile / 'fixture.json').write_text(json.dumps(self.fixture))

    def test_cli_relogin_clears_previous_online_verification(self):
        self.store.cache(self.account['id'], 'online', {'state': 'verified', 'checked_at': 1})
        p = subprocess.run([sys.executable, str(REPO / 'manager.py'), '--root', str(self.store.root),
                            'login', self.account['id']], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        data = self.store.cached()[self.account['id']]
        self.assertEqual(data['auth']['email'], 'new@example.com')
        self.assertFalse(data.get('online'))

    def test_external_identity_change_invalidates_cached_online_status(self):
        self.store.cache(self.account['id'], 'auth', {'state': 'saved', 'email': 'old@example.com'})
        self.store.cache(self.account['id'], 'online', {'state': 'verified', 'checked_at': 1})
        refresh_account(self.store, Claude(self.fake), self.account)
        self.assertFalse(self.store.cached()[self.account['id']].get('online'))

    def test_closing_overlay_terminates_its_detached_probe(self):
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                pid_file = self.root / ('probe-' + str(sig))
                self.fixture.update(sleep=30, pid_file=str(pid_file))
                self.write_fixture()
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 28, 100, 0, 0))
                proc = subprocess.Popen([sys.executable, str(REPO / 'manager.py'), '--root', str(self.store.root), 'ui'],
                                        stdin=slave, stdout=slave, stderr=slave,
                                        env=dict(os.environ, TERM='xterm-256color'))
                child_pid = None
                try:
                    deadline = time.monotonic() + 5
                    while not pid_file.exists() and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.05)[0]:
                            os.read(master, 65536)
                    self.assertTrue(pid_file.exists())
                    child_pid = int(pid_file.read_text())
                    proc.send_signal(sig)
                    deadline = time.monotonic() + 3
                    while proc.poll() is None and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.05)[0]:
                            os.read(master, 65536)
                    self.assertIsNotNone(proc.poll())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child_pid, 0)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()
                    if child_pid:
                        try:
                            os.kill(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    os.close(master)
                    os.close(slave)

    def test_add_then_enter_selects_new_account_not_previously_highlighted_one(self):
        self.fake.write_text('#!' + sys.executable + '\n' + '''
import json, sys
if sys.argv[1:3] == ['auth', 'status']:
    print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'email': 'new@example.com'}))
elif sys.argv[1:2] == ['agents']:
    print('[]')
''')
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 35, 100, 0, 0))
        proc = subprocess.Popen([sys.executable, str(REPO / 'manager.py'), '--root', str(self.store.root), 'ui'],
                                stdin=slave, stdout=slave, stderr=slave,
                                env=dict(os.environ, TERM='xterm-256color'))
        output = b''
        def pump():
            nonlocal output
            if select.select([master], [], [], 0.05)[0]:
                output += os.read(master, 65536)
        def wait_for(condition):
            deadline = time.monotonic() + 5
            while not condition() and time.monotonic() < deadline:
                pump()
            self.assertTrue(condition(), repr(output[-500:]))
        try:
            wait_for(lambda: b'Sessions:' in output)
            os.write(master, b'a')
            wait_for(lambda: b'New account name' in output)
            os.write(master, b'New\n')
            wait_for(lambda: len(self.store.read()['accounts']) == 2)
            new_id = self.store.read()['accounts'][1]['id']
            wait_for(lambda: self.store.cached().get(new_id, {}).get('auth', {}).get('state') == 'saved')
            # Curses emits incremental updates; retain output and wait for login completion.
            wait_for(lambda: b'Press Enter to select this account.' in output)
            os.write(master, b'\n')
            deadline = time.monotonic() + 2
            while self.store.read()['selected'] is None and time.monotonic() < deadline:
                pump()
            self.assertEqual(self.store.read()['selected'], new_id)
        finally:
            os.write(master, b'q')
            deadline = time.monotonic() + 2
            while proc.poll() is None and time.monotonic() < deadline:
                pump()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)
            os.close(slave)


if __name__ == '__main__':
    unittest.main()

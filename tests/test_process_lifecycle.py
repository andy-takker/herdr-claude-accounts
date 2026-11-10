import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from accounts import Store
from claude_cli import termination_signals


REPO = Path(__file__).resolve().parents[1]


class ProcessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / 'claude'
        self.binary.write_text('#!' + sys.executable + '\n' + '''
import json, os, pathlib, subprocess, sys, time
root = pathlib.Path(os.environ['TEST_ROOT'])
args = sys.argv[1:]
stage = ('auth' if args[:2] == ['auth', 'status'] else
         'login' if args[:2] == ['auth', 'login'] else
         'agents' if args[:1] == ['agents'] else
         'usage' if '--input-format' in args else 'online')
with (root / 'calls').open('a') as out:
    out.write(stage + '\\n')
if stage == os.environ['WAIT_STAGE']:
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    (root / 'pids').write_text(json.dumps([os.getpid(), child.pid]))
    time.sleep(30)
if stage == 'auth':
    print(json.dumps({'loggedIn': True, 'authMethod': 'claude.ai'}))
elif stage == 'agents':
    print('[]')
elif stage == 'online':
    print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False}))
''')
        self.binary.chmod(0o700)

    def signal_command(self, command, stage, sig):
        self.store = Store(self.root / ('data-' + str(sig) + '-' + command[0] + '-' + stage))
        self.account = self.store.create('Test')
        self.store.configure_binary(self.binary)
        command = [self.account['id'] if arg == 'ACCOUNT' else arg for arg in command]
        for name in ('pids', 'calls'):
            (self.root / name).unlink(missing_ok=True)
        env = dict(PATH=os.environ['PATH'], HOME=str(self.root),
                   TEST_ROOT=str(self.root), WAIT_STAGE=stage)
        proc = subprocess.Popen(
            [sys.executable, str(REPO / 'manager.py'), '--root', str(self.store.root)] + command,
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                     start_new_session=True)
        pids = []
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    pids = json.loads((self.root / 'pids').read_text())
                    break
                except (FileNotFoundError, ValueError):
                    time.sleep(0.01)
            self.assertTrue(pids, 'Fake Claude did not reach ' + stage)
            proc.send_signal(sig)
            stdout, stderr = proc.communicate(timeout=5)
            calls = (self.root / 'calls').read_text().splitlines()
            if sig == signal.SIGINT:
                self.assertEqual(calls, ['auth'], 'Interrupted auth started a paid check')
            for pid in pids:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.01)
                else:
                    self.fail('Owned subprocess survived shutdown: ' + str(pid))
            self.assertIsNone(unrelated.poll(), 'An unrelated process was terminated')
            self.assertEqual(proc.returncode, 128 + sig, stderr)
            self.assertEqual(stdout, '')
            return calls
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
            if pids:
                try:
                    os.killpg(pids[0], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            unrelated.kill()
            unrelated.wait()

    def test_cli_termination_cleans_up_detached_commands(self):
        cases = [(['status'], 'agents'), (['status'], 'auth'),
                 (['sessions'], 'agents'), (['check', 'ACCOUNT'], 'auth'),
                 (['check', 'ACCOUNT'], 'online'),
                 (['refresh-usage', 'ACCOUNT'], 'usage'),
                 (['login', 'ACCOUNT', '--browser'], 'login')]
        for sig in (signal.SIGTERM, signal.SIGHUP):
            for command, stage in cases:
                with self.subTest(signal=sig, command=command, stage=stage):
                    self.signal_command(command, stage, sig)

    def test_interrupt_during_auth_never_starts_paid_check(self):
        calls = self.signal_command(['check', 'ACCOUNT'], 'auth', signal.SIGINT)
        self.assertEqual(calls, ['auth'])
        self.assertNotIn('online', self.store.cached().get(self.account['id'], {}))

    def test_nested_signal_scope_restores_previous_handlers_after_exit(self):
        signals = (signal.SIGTERM, signal.SIGHUP)
        previous = {sig: signal.getsignal(sig) for sig in signals}
        with termination_signals():
            outer = {sig: signal.getsignal(sig) for sig in signals}
            for sig in signals:
                with self.assertRaises(SystemExit) as caught:
                    with termination_signals():
                        os.kill(os.getpid(), sig)
                self.assertEqual(caught.exception.code, 128 + sig)
                self.assertEqual({s: signal.getsignal(s) for s in signals}, outer)
        self.assertEqual({sig: signal.getsignal(sig) for sig in signals}, previous)


if __name__ == '__main__':
    unittest.main()

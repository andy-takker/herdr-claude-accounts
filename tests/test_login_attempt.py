import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from accounts import Store, AccountError
from login_attempt import LoginAttempt

REPO = Path(__file__).resolve().parents[1]


class LoginCancellationTests(unittest.TestCase):
    def test_ticket_cannot_be_reused_for_another_profile_or_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp); a=store.create('A'); b=store.create('B')
            ticket=LoginAttempt.prepare(store,a['id'])['attempt']
            with self.assertRaises(AccountError):
                with LoginAttempt(store,ticket).running(b['id']): self.fail('wrong profile accepted')
            with self.assertRaises(AccountError): LoginAttempt(store,'../../outside').cancel()
            with LoginAttempt(store,ticket).running(a['id']) as first:
                self.assertFalse(first.is_set())
                with self.assertRaises(AccountError):
                    with LoginAttempt(store,ticket).running(a['id']): self.fail('duplicate login accepted')
            self.assertFalse(LoginAttempt(store,ticket).cancel()['cancelled'])

    def test_cancel_before_start_and_during_login_preserves_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / 'data')
            binary = root / 'claude'
            binary.write_bytes((REPO / 'tests/fake_claude.py').read_bytes()); binary.chmod(0o700)
            store.configure_binary(binary)
            account = store.create('Work'); profile = store.profile(account['id'])
            pid = root / 'pid'
            (profile / 'fixture.json').write_text(json.dumps({'sleep':30,'pid_file':str(pid)}))
            (profile / 'history.jsonl').write_text('keep')
            command = [sys.executable, str(REPO/'manager.py'), '--root', str(store.root)]
            def cli(*args):
                p = subprocess.run(command+list(args),text=True,capture_output=True,timeout=5)
                self.assertEqual(p.returncode,0,p.stderr)
                return json.loads(p.stdout)
            first = cli('prepare-login',account['id'])['attempt']
            cli('cancel-login',first)
            self.assertEqual(cli('login',account['id'],'--browser','--attempt',first)['state'],'cancelled')
            self.assertFalse(pid.exists())
            second = cli('prepare-login',account['id'])['attempt']
            process = subprocess.Popen(command+['login',account['id'],'--browser','--attempt',second],
                                       stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                deadline=time.monotonic()+5
                while not pid.exists() and time.monotonic()<deadline: time.sleep(0.02)
                self.assertTrue(pid.exists())
                self.assertFalse(cli('cancel-login',first)['cancelled'])
                self.assertIsNone(process.poll())
                cli('cancel-login',second)
                out,err=process.communicate(timeout=5)
                self.assertEqual(process.returncode,0,err)
                self.assertEqual(json.loads(out)['state'],'cancelled')
                with self.assertRaises(ProcessLookupError): os.kill(int(pid.read_text()),0)
            finally:
                if process.poll() is None: process.kill()
                process.communicate()
            self.assertEqual((profile/'history.jsonl').read_text(),'keep')
            self.assertEqual(store.read()['accounts'],[account])

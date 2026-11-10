import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

from accounts import Store

REPO = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data')
        self.binary = self.root / 'upstream'
        self.binary.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys, time
if '--wait' in sys.argv:
    print('ready', flush=True)
    time.sleep(30)
else:
    print(json.dumps({'args': sys.argv[1:], 'profile': os.environ.get('CLAUDE_CONFIG_DIR'),
                      'proxy': os.environ.get('HTTPS_PROXY'), 'stdin': sys.stdin.read()}))
sys.exit(7)
''')
        self.binary.chmod(0o700)
        self.store.configure_binary(self.binary)
        self.account = self.store.create('Work')
        self.store.select(self.account['id'])
        self.env = {'PATH': os.environ['PATH'], 'HTTPS_PROXY': 'http://my-proxy'}
        self.command = [sys.executable, '-c',
                        'import sys; from accounts import Store; from launcher import launch; '
                        'launch(Store(sys.argv[1]), sys.argv[2:])', str(self.store.root)]

    def run_launch(self, *args, env=None):
        return subprocess.run(self.command + list(args), cwd=REPO, env=env or self.env,
                              input='hello\n', text=True, capture_output=True, timeout=5)

    def test_selected_profile_arguments_stdin_and_exit_code_reach_upstream(self):
        p = self.run_launch('two words', '$(not a command)', '--model', 'sonnet')
        self.assertEqual(p.returncode, 7, p.stderr)
        d = json.loads(p.stdout)
        self.assertEqual(d['args'], ['two words', '$(not a command)', '--model', 'sonnet'])
        self.assertEqual(d['stdin'], 'hello\n')
        self.assertEqual(d['proxy'], 'http://my-proxy')
        self.assertEqual(d['profile'], str(self.store.profile(self.account['id'])))

    def test_system_default_and_explicit_config_preserve_user_auth_environment(self):
        self.store.select(None)
        p = self.run_launch(env=dict(self.env, ANTHROPIC_API_KEY='private'))
        self.assertIsNone(json.loads(p.stdout)['profile'])
        self.store.select(self.account['id'])
        p = self.run_launch(env=dict(self.env, CLAUDE_CONFIG_DIR='/custom', ANTHROPIC_API_KEY='private'))
        self.assertEqual(json.loads(p.stdout)['profile'], '/custom')

    def test_new_selection_does_not_change_running_session(self):
        a = json.loads(self.run_launch().stdout)['profile']
        second = self.store.create('Other')
        self.store.select(second['id'])
        b = json.loads(self.run_launch().stdout)['profile']
        self.assertNotEqual(a, b)
        self.assertEqual(a, str(self.store.profile(self.account['id'])))

    def test_pinned_launch_ignores_inherited_routing_without_changing_selection(self):
        second = self.store.create('Other')
        command = [sys.executable, str(REPO / 'manager.py'), '--root', str(self.store.root), 'launch']
        for identity, expected in [(second['id'], str(self.store.profile(second['id']))), ('system', None)]:
            p = subprocess.run(command + [identity, '--', '--model', 'sonnet'], text=True,
                               input='', capture_output=True, env=dict(self.env, CLAUDE_CONFIG_DIR='/other'))
            self.assertEqual(p.returncode, 7, p.stderr)
            result = json.loads(p.stdout)
            self.assertEqual(result['profile'], expected)
            self.assertEqual(result['args'], ['--model', 'sonnet'])
            self.assertEqual(self.store.read()['selected'], self.account['id'])

    def test_missing_selected_profile_and_conflicting_auth_fail_closed(self):
        p = self.run_launch(env=dict(self.env, ANTHROPIC_API_KEY='SECRET'))
        self.assertNotEqual(p.returncode, 7)
        self.assertNotIn('SECRET', p.stderr)
        self.store.profile(self.account['id']).rmdir()
        p = self.run_launch()
        self.assertNotEqual(p.returncode, 7)
        self.assertEqual(p.stdout, '')

    def test_recursive_upstream_is_rejected(self):
        own = self.store.root / 'bin' / 'claude'
        own.parent.mkdir()
        own.write_text('#!/bin/sh\nexit 42\n')
        own.chmod(0o700)
        self.store.configure_binary(own)
        p = self.run_launch()
        self.assertNotEqual(p.returncode, 42)

    def test_shared_settings_reach_launch_new_profiles_and_system_default(self):
        source = self.root / '.claude'
        source.mkdir()
        (source / 'settings.json').write_text('{"model":"opus"}')
        (self.root / '.claude.json').write_text('{"hasCompletedOnboarding":true}')
        self.store.enable_shared_settings(self.root)
        profile = self.store.profile(self.account['id'])
        (profile / 'settings.json').write_text('{"model":"sonnet"}')
        self.assertEqual(self.run_launch().returncode, 7)
        self.assertEqual(json.loads((source / 'settings.json').read_text())['model'], 'sonnet')
        second = self.store.create('Other')
        second_profile = self.store.profile(second['id'])
        self.assertTrue(json.loads((second_profile / '.claude.json').read_text())['hasCompletedOnboarding'])
        self.assertEqual(json.loads((second_profile / 'settings.json').read_text())['model'], 'sonnet')
        self.store.select(None)
        (profile / 'settings.json').write_text('{"model":"haiku"}')
        self.assertEqual(self.run_launch().returncode, 7)
        self.assertEqual(json.loads((source / 'settings.json').read_text())['model'], 'haiku')

        # An explicit unrelated profile bypasses the manager even if shared files are invalid.
        (source / 'settings.json').write_text('{')
        self.assertEqual(self.run_launch(env=dict(self.env, CLAUDE_CONFIG_DIR='/custom')).returncode, 7)
        self.assertNotEqual(self.run_launch().returncode, 7)

    def test_signal_reaches_exec_replacement(self):
        p = subprocess.Popen(self.command + ['--wait'], cwd=REPO, env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), 'ready')
            p.send_signal(signal.SIGTERM)
            self.assertEqual(p.wait(timeout=3), -signal.SIGTERM)
        finally:
            if p.poll() is None:
                p.kill()
            p.communicate()


class ManagerTests(unittest.TestCase):
    def test_native_login_requires_confirmed_identity_and_emits_only_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'data')
            binary = Path(tmp) / 'claude'
            binary.write_bytes((REPO / 'tests/fake_claude.py').read_bytes())
            binary.chmod(0o700)
            store.configure_binary(binary)
            account = store.create('Work')
            profile = store.profile(account['id'])
            command = [sys.executable, str(REPO / 'manager.py'), '--root', str(store.root),
                       'login', account['id'], '--browser']
            for auth, success in [({'loggedIn':False}, False),
                                  ({'loggedIn':True,'authMethod':'claude.ai','email':'work@example.com'}, True)]:
                (profile / 'fixture.json').write_text(json.dumps({'exit':0,'auth':auth}))
                p = subprocess.run(command, text=True, capture_output=True)
                self.assertEqual(p.returncode == 0, success, p.stderr)
                if success:
                    self.assertEqual(json.loads(p.stdout)['email'], 'work@example.com')
                else:
                    self.assertEqual(p.stdout, '')
                    self.assertTrue(profile.is_dir())

    def test_cli_profile_selection_and_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = [sys.executable, str(REPO / 'manager.py'), '--root', tmp]
            def run(*args):
                p = subprocess.run(command + list(args), text=True, capture_output=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                return json.loads(p.stdout)
            a = run('add', 'Work', '--no-login')
            run('select', a['id'])
            rows = run('list')
            self.assertEqual(rows['selected'], a['id'])
            self.assertEqual(rows['accounts'][0]['label'], 'Work')
            run('select', 'system')
            self.assertIsNone(run('list')['selected'])
            run('select', a['id'])
            removed = run('remove', a['id'])
            self.assertIsNone(removed['selected'])
            self.assertEqual(run('list')['accounts'], [])


if __name__ == '__main__':
    unittest.main()

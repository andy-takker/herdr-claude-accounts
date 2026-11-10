import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from accounts import AccountError
from claude_cli import Claude, managed_environment


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profile'
        self.profile.mkdir()
        self.binary = self.root / 'claude'
        shutil.copyfile(Path(__file__).with_name('fake_claude.py'), self.binary)
        self.binary.chmod(0o700)
        self.cli = Claude(self.binary, env={'PATH': os.environ['PATH']}, timeout=2)

    def fixture(self, **data):
        (self.profile / 'fixture.json').write_text(json.dumps(data))

    def test_local_signin_is_not_claimed_as_online_verification(self):
        self.fixture(exit=0, auth={'loggedIn': True, 'authMethod': 'claude.ai',
                                  'email': 'work@example.com', 'token': 'SECRET'})
        result = self.cli.auth(self.profile)
        self.assertEqual(result['state'], 'saved')
        self.assertEqual(result['email'], 'work@example.com')
        self.assertEqual(result['source'], 'local')
        self.assertNotIn('SECRET', json.dumps(result))
        self.fixture(exit=1)
        self.assertEqual(self.cli.auth(self.profile)['state'], 'needs_login')

    def test_cli_failure_and_malformed_output_are_unknown_not_logged_out(self):
        for data in ({'exit': 2}, {'malformed': True, 'exit': 1},
                     {'exit': 0, 'auth': {'loggedIn': 'true'}},
                     {'exit': 0, 'auth': {'loggedIn': True, 'authMethod': 'api_key'}}):
            self.fixture(**data)
            result = self.cli.auth(self.profile)
            self.assertEqual(result['state'], 'unknown')
            self.assertNotIn('SECRET', json.dumps(result))

    def test_timeout_is_bounded_and_unknown(self):
        self.fixture(sleep=3)
        cli = Claude(self.binary, env={'PATH': os.environ['PATH']}, timeout=0.05)
        self.assertEqual(cli.auth(self.profile)['state'], 'unknown')
        with patch.object(cli, '_json', return_value=(None, None)):
            result = cli.check(self.profile)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['reason'], 'timeout')

    def test_background_poll_can_be_cancelled_without_leaving_a_waiting_ui(self):
        self.fixture(sleep=3)
        event = threading.Event()
        cli = Claude(self.binary, env={'PATH': os.environ['PATH']}, cancel=event)
        timer = threading.Timer(0.1, event.set)
        timer.start()
        start = time.monotonic()
        try:
            self.assertEqual(cli.auth(self.profile)['state'], 'unknown')
            self.assertLess(time.monotonic() - start, 1.5)
        finally:
            timer.cancel()

    def test_online_result_separates_auth_rate_limit_permission_and_network(self):
        cases = [
            ({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'OK'}, 0, 'verified'),
            ({'error': {'type': 'authentication_error'}}, 1, 'needs_login'),
            ({'error': {'type': 'rate_limit_error'}}, 1, 'limited'),
            ({'error': {'type': 'permission_error'}}, 1, 'access_denied'),
            ({'error': {'type': 'connection_error', 'message': 'SECRET'}}, 1, 'network_error'),
            ({'type': 'result', 'subtype': 'success', 'is_error': True,
              'result': 'API Error: SECRET'}, 1, 'unknown'),
            ({'type': 'result', 'subtype': 'success', 'is_error': False}, 2, 'unknown'),
        ]
        for payload, code, expected in cases:
            self.fixture(online=payload, exit=code)
            result = self.cli.check(self.profile)
            self.assertEqual(result['state'], expected)
            self.assertNotIn('SECRET', json.dumps(result))

    def test_sessions_are_filtered_and_failure_is_distinct_from_empty(self):
        self.fixture(sessions=[{'id': 'abc', 'sessionId': 'long-id', 'kind': 'background',
                               'state': 'working', 'cwd': '/project', 'name': 'Private prompt',
                               'startedAt': 123, 'transcript': 'SECRET'}])
        result = self.cli.sessions(self.profile)
        self.assertTrue(result['available'])
        self.assertEqual(result['sessions'][0]['state'], 'working')
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('Private prompt', json.dumps(result))
        self.fixture(sessions=[])
        self.assertEqual(self.cli.sessions(self.profile), {'available': True, 'sessions': []})
        self.fixture(sessions=[], exit=1)
        self.assertFalse(self.cli.sessions(self.profile)['available'])

    def test_real_cli_stream_auth_error_is_not_hidden_by_final_result(self):
        self.fixture(exit=1, online=[
            {'type': 'system', 'subtype': 'init'},
            {'type': 'assistant', 'error': 'authentication_failed'},
            {'type': 'result', 'subtype': 'success', 'is_error': True},
        ])
        self.assertEqual(self.cli.check(self.profile)['state'], 'needs_login')
        self.fixture(sessions=[{'not': 'a session'}])
        self.assertFalse(self.cli.sessions(self.profile)['available'])

    def test_login_cancellation_preserves_the_profile(self):
        self.fixture(exit=130)
        self.assertEqual(self.cli.login(self.profile), 130)
        self.assertTrue(self.profile.is_dir())

    def test_system_identity_ignores_inherited_profile_routing(self):
        system = self.root / '.claude'
        system.mkdir()
        (system / 'fixture.json').write_text(json.dumps({'exit': 0, 'auth': {
            'loggedIn': True, 'authMethod': 'claude.ai', 'email': 'system@example.com'}}))
        self.fixture(exit=0, auth={'loggedIn': True, 'authMethod': 'claude.ai',
                                  'email': 'managed@example.com'})
        cli = Claude(self.binary, env=dict(self.cli.env, HOME=str(self.root),
                     CLAUDE_CONFIG_DIR=str(self.profile)))
        self.assertEqual(cli.auth(None)['email'], 'system@example.com')
        self.assertEqual(cli.auth(self.profile)['email'], 'managed@example.com')

    def test_interactive_sessions_use_session_id_and_status(self):
        self.fixture(sessions=[{'pid': 123, 'sessionId': 'interactive', 'status': 'busy',
                                'kind': 'interactive', 'name': 'PRIVATE'},
                               {'id': 'background', 'state': 'blocked'}])
        result = self.cli.sessions(self.profile)
        self.assertTrue(result['available'])
        self.assertEqual([r['state'] for r in result['sessions']], ['busy', 'blocked'])
        self.assertEqual(result['sessions'][0]['id'], 'interactive')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_browser_login_captures_progress_and_times_out(self):
        self.fixture(exit=0)
        self.assertEqual(self.cli.login(self.profile, browser=True), 0)
        self.fixture(sleep=3)
        cli = Claude(self.binary, env=self.cli.env, timeout=0.05)
        with self.assertRaisesRegex(AccountError, 'timed out'):
            cli.login(self.profile, browser=True, timeout=0.05)

    def test_managed_env_keeps_proxy_and_rejects_conflicting_auth_without_values(self):
        env = managed_environment(self.profile, {'HTTPS_PROXY': 'http://proxy', 'PATH': '/bin'})
        self.assertEqual(env['HTTPS_PROXY'], 'http://proxy')
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(self.profile))
        for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN',
                     'CLAUDE_CODE_USE_BEDROCK', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_PROFILE'):
            with self.assertRaises(AccountError) as ctx:
                managed_environment(self.profile, {name: 'SECRET'})
            self.assertNotIn('SECRET', str(ctx.exception))

    def test_usage_control_request_never_sends_a_prompt_and_filters_private_data(self):
        self.fixture(usage={'rate_limits_available':True,'rate_limits':{'limits':[
            {'kind':'session','group':'session','percent':28,'resets_at':'2026-11-10T18:09:59+00:00','scope':None},
            {'kind':'weekly_all','group':'weekly','percent':93,'resets_at':'2026-11-11T10:59:59+00:00','scope':None},
            {'kind':'weekly_scoped','group':'weekly','percent':67,'resets_at':None,
             'scope':{'model':{'id':None,'display_name':'Fable'},'surface':None}}]},
            'behaviors':{'private':'SECRET'}})
        result=self.cli.usage(self.profile)
        self.assertEqual(result['state'],'available')
        self.assertEqual([w['used_percent'] for w in result['windows']],[28,93,67])
        self.assertEqual(result['windows'][2]['label'],'Weekly · Fable')
        self.assertNotIn('SECRET',json.dumps(result))
        self.fixture(usage={'rate_limits_available':False})
        self.assertEqual(self.cli.usage(self.profile)['state'],'unavailable')
        self.fixture(usage={'rate_limits_available':True,'rate_limits':{'limits':[
            {'kind':'session','group':'session','percent':True}]}})
        self.assertEqual(self.cli.usage(self.profile)['reason'],'invalid_response')

    def test_browser_login_cancels_only_its_own_process(self):
        pid_file=self.root/'pid'
        self.fixture(sleep=30,pid_file=str(pid_file))
        class CancelAfterStart:
            def is_set(self): return pid_file.exists() and bool(pid_file.read_text())
        self.assertEqual(self.cli.login(self.profile,browser=True,cancel=CancelAfterStart(),timeout=5),130)
        with self.assertRaises(ProcessLookupError): os.kill(int(pid_file.read_text()),0)
        self.assertTrue(self.profile.is_dir())


if __name__ == '__main__':
    unittest.main()

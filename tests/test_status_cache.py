import tempfile
import threading
import unittest
from unittest.mock import patch

from accounts import Store
from manager import refresh_account, check_account, login_account


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.account = self.store.create('Work')
        self.ident = self.account['id']
        self.calls = []
        outer = self
        class Client:
            binary = '/bin/fixture'
            cancel = threading.Event()
            email = 'old@example.test'
            def auth(self, profile):
                outer.calls.append('auth')
                if self.cancel.is_set():
                    return {'state': 'unknown', 'reason': 'cancelled'}
                return {'state': 'saved', 'email': self.email, 'orgId': 'org'}
            def sessions(self, profile):
                outer.calls.append('sessions')
                return {'available': True, 'sessions': [{'id': 'active', 'state': 'idle'}]}
            def check(self, profile):
                outer.calls.append('check')
                return {'state': 'verified'}
            def login(self, profile, **kwargs):
                self.email = 'new@example.test'
                return 0
        self.cli = Client()

    def test_cancelled_refresh_keeps_auth_and_verification(self):
        self.store.cache(self.ident, 'auth', self.cli.auth(None))
        self.store.cache(self.ident, 'online', {'state': 'verified'})
        before = self.store.cached()
        def cancel(profile):
            self.cli.cancel.set()
            return {'available': False, 'sessions': []}
        with patch.object(self.cli, 'sessions', side_effect=cancel):
            refresh_account(self.store, self.cli, self.account)
        self.assertEqual(self.store.cached(), before)

    def test_late_check_cannot_verify_relogged_identity(self):
        def old_check(profile):
            login_account(self.store, self.cli, self.ident, browser=True)
            return {'state': 'verified'}
        with patch.object(self.cli, 'check', side_effect=old_check):
            check_account(self.store, self.cli, self.ident)
        self.assertFalse(self.store.cached()[self.ident].get('online'))
        self.assertEqual(self.store.cached()[self.ident]['auth']['email'], 'new@example.test')

    def test_check_observing_external_identity_change_invalidates_warm_snapshot(self):
        refresh_account(self.store, self.cli, self.account)
        self.store.cache(self.ident, 'online', {'state': 'verified'})
        def change_identity(profile):
            self.cli.email = 'external@example.test'
            return {'state': 'verified'}
        with patch.object(self.cli, 'check', side_effect=change_identity):
            result = check_account(self.store, self.cli, self.ident)
        self.assertEqual(result['reason'], 'account_changed')
        current = refresh_account(self.store, self.cli, self.account)
        self.assertEqual(current['auth']['email'], 'external@example.test')
        self.assertFalse(current['online'])

    def test_sessions_only_refresh_cannot_rebind_old_auth_to_new_binary(self):
        refresh_account(self.store, self.cli, self.account)
        self.cli.binary = '/bin/new-fixture'
        self.cli.email = 'new-binary@example.test'
        refresh_account(self.store, self.cli, self.account, local=False)
        result = refresh_account(self.store, self.cli, self.account)
        self.assertEqual(result['auth']['email'], 'new-binary@example.test')

    def test_warm_refresh_shared_between_instances_and_system(self):
        for account in (self.account, {'id': None, 'label': 'System default'}):
            refresh_account(self.store, self.cli, account)
            self.calls.clear()
            row = refresh_account(Store(self.tmp.name), self.cli, account)
            self.assertEqual(self.calls, [])
            self.assertEqual(row['sessions']['sessions'][0]['id'], 'active')

    def test_concurrent_refresh_returns_snapshot_without_duplicate_probe(self):
        entered, release = threading.Event(), threading.Event()
        def blocked(profile):
            entered.set()
            self.assertTrue(release.wait(5))
            return {'available': True, 'sessions': []}
        with patch.object(self.cli, 'sessions', side_effect=blocked) as sessions:
            worker = threading.Thread(target=refresh_account, args=(self.store, self.cli, self.account))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                second = refresh_account(Store(self.tmp.name), self.cli, self.account)
                self.assertEqual(sessions.call_count, 1)
                self.assertEqual(second['id'], self.ident)
            finally:
                release.set(); worker.join(5)

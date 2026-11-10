import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from accounts import Store, AccountError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'data'
        self.store = Store(self.root)

    def test_partial_creation_returns_retained_profile_id_and_warning(self):
        with patch.object(self.store, 'sync_settings', side_effect=AccountError('Settings conflict')):
            account = self.store.create('Work')
        self.assertIn('Settings conflict', account['warning'])
        self.assertEqual(self.store.read()['accounts'], [{'id': account['id'], 'label': 'Work'}])
        self.assertTrue(self.store.profile(account['id']).is_dir())

    def test_history_migration_is_explicit_then_shared_for_future_profiles(self):
        account = self.store.create('Existing')
        home = Path(self.tmp.name) / 'home'
        self.store.enable_shared_settings(home)
        profile = self.store.profile(account['id'])
        self.assertFalse((profile / 'projects').is_symlink())
        result = self.store.enable_shared_conversations()
        self.assertEqual(result['shared_profiles'], 1)
        self.assertTrue(self.store.read()['share_conversations'])
        newer = self.store.create('New')
        self.assertEqual((self.store.profile(newer['id']) / 'projects').resolve(), (home / '.claude/projects').resolve())

    def test_profiles_are_private_and_selection_persists(self):
        a = self.store.create('Work')
        b = self.store.create('Personal')
        self.assertNotEqual(a['id'], b['id'])
        self.assertEqual(self.store.read()['selected'], None)
        self.store.select(a['id'])
        self.assertEqual(Store(self.root).read()['selected'], a['id'])
        self.assertEqual(self.store.profile(a['id']).stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / 'accounts.json').stat().st_mode & 0o777, 0o600)
        self.store.select(None)
        self.assertIsNone(self.store.read()['selected'])

    def test_invalid_names_and_ids_do_not_change_selection(self):
        for label in ('', '  ', '\x1b[2J', 'line\nnext'):
            with self.assertRaises(AccountError):
                self.store.create(label)
        a = self.store.create('OK')
        self.store.select(a['id'])
        for value in ('../../outside', 'missing'):
            with self.assertRaises(AccountError):
                self.store.select(value)
        self.assertEqual(self.store.read()['selected'], a['id'])

    def test_missing_or_symlinked_profile_is_not_used(self):
        a = self.store.create('Work')
        p = self.store.profile(a['id'])
        p.rmdir()
        with self.assertRaises(AccountError):
            self.store.select(a['id'])
        p.symlink_to(self.root)
        with self.assertRaises(AccountError):
            self.store.profile(a['id'])

    def test_corrupt_state_and_unknown_selection_are_rejected(self):
        self.store.create('Work')
        f = self.root / 'accounts.json'
        for text in ('{', '[]', '{"version": 99}', json.dumps({
            'version': 1, 'accounts': [], 'selected': 'unknown', 'binary': None
        })):
            f.write_text(text)
            with self.assertRaises(AccountError):
                self.store.read()

    def test_parallel_additions_and_status_updates_do_not_get_lost(self):
        def create(n):
            return Store(self.root).create('Account ' + str(n))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            accounts = list(pool.map(create, range(20)))
        self.assertEqual(len(self.store.read()['accounts']), 20)
        def cache(a):
            self.store.cache(a['id'], 'auth', {'state': 'saved'})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(cache, accounts))
        self.assertEqual(len(self.store.cached()), 20)

    def test_status_cannot_overwrite_selection_or_executable(self):
        a = self.store.create('Work')
        self.store.configure_binary('/bin/echo')
        self.store.select(a['id'])
        self.store.cache(a['id'], 'auth', {'state': 'needs_login'})
        self.assertEqual(self.store.read()['binary'], '/bin/echo')
        self.assertEqual(self.store.read()['selected'], a['id'])
        self.assertEqual(self.store.cached()[a['id']]['auth']['state'], 'needs_login')

    def test_remove_always_selects_system_and_retains_profile_for_running_sessions(self):
        a = self.store.create('Work')
        b = self.store.create('Personal')
        profile = self.store.profile(a['id'])
        (profile / 'history.jsonl').write_text('session history')
        shared = self.root / 'shared'
        shared.mkdir()
        (shared / 'SKILL.md').write_text('shared instructions')
        (profile / 'skills').symlink_to(shared)
        self.store.select(b['id'])
        result = self.store.remove(a['id'])
        self.assertIsNone(self.store.read()['selected'])
        self.assertEqual(self.store.read()['accounts'], [b])
        self.assertEqual((profile / 'history.jsonl').read_text(), 'session history')
        self.assertEqual((shared / 'SKILL.md').read_text(), 'shared instructions')
        self.assertTrue((profile / 'skills').is_symlink())
        self.assertEqual(json.loads(Path(result['record']).read_text())['account'], a)
        self.assertEqual(Path(result['record']).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(AccountError):
            self.store.select(a['id'])
        self.store.select(b['id'])
        with self.assertRaises(AccountError):
            self.store.remove('../../outside')
        self.assertEqual(self.store.read()['selected'], b['id'])
        self.store.remove(b['id'])
        self.assertEqual(self.store.read()['accounts'], [])
        self.assertIsNone(self.store.read()['selected'])

    def test_restore_keeps_identity_data_and_current_selection(self):
        a = self.store.create('Work')
        profile = self.store.profile(a['id'])
        (profile / 'history.jsonl').write_text('history')
        self.store.remove(a['id'])
        b = self.store.create('Other')
        self.store.select(b['id'])
        self.assertEqual(self.store.removed()[0]['account'], a)
        self.assertEqual(self.store.restore(a['id']), a)
        self.assertEqual(self.store.read()['selected'], b['id'])
        self.assertEqual((self.store.profile(a['id']) / 'history.jsonl').read_text(), 'history')
        self.assertEqual(self.store.removed(), [])
        with self.assertRaises(AccountError):
            self.store.restore(a['id'])

    def test_restore_rejects_forged_record_and_symlink_profile(self):
        a = self.store.create('Work')
        profile = self.store.profile(a['id'])
        result = self.store.remove(a['id'])
        record = Path(result['record'])
        data = json.loads(record.read_text())
        data['account']['id'] = '../outside'
        record.write_text(json.dumps(data))
        with self.assertRaises(AccountError):
            self.store.restore(a['id'])
        data['account'] = a
        record.write_text(json.dumps(data))
        profile.rmdir()
        profile.symlink_to(self.root)
        with self.assertRaises(AccountError):
            self.store.restore(a['id'])
        self.assertEqual(self.store.read()['accounts'], [])

    def test_restore_reports_retained_success_when_settings_cannot_sync(self):
        account = self.store.create('Work')
        self.store.remove(account['id'])
        with patch.object(self.store, 'sync_settings', side_effect=AccountError('Settings locked')):
            with self.assertRaisesRegex(AccountError, 'restored.*Settings locked'):
                self.store.restore(account['id'])
        self.assertEqual(self.store.read()['accounts'], [account])
        self.assertEqual(self.store.removed(), [])
        self.assertIsNone(self.store.read()['selected'])



if __name__ == '__main__':
    unittest.main()

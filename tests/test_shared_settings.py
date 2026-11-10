import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from accounts import Store, AccountError
from shared_settings import synchronize


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def get(path):
    return json.loads(path.read_text())


class SharedSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.source = self.home / 'personal'
        self.source.mkdir()
        self.source_state = self.source / '.claude.json'
        self.store = Store(self.home / 'data')
        self.a = self.store.create('A')
        self.b = self.store.create('B')
        self.pa = self.store.profile(self.a['id'])
        self.pb = self.store.profile(self.b['id'])

    def sync(self):
        return synchronize(self.store, self.source, self.source_state)

    def test_initial_settings_trust_and_extensions_share_without_copying_account_auth(self):
        put(self.source / 'settings.json', {'theme': 'light', 'model': 'opus',
            'hooks': {'SessionStart': []}, 'permissions': {'allow': ['Read']}})
        put(self.source_state, {'theme': 'light', 'hasCompletedOnboarding': True,
            'oauthAccount': {'emailAddress': 'source@example.com'}, 'userID': 'source-user',
            'primaryApiKey': 'SOURCE_SECRET', 'modelAccessCache': {'source': True},
            'mcpServers': {'local': {'command': 'server'}},
            'projects': {'/repo': {'hasTrustDialogAccepted': True, 'allowedTools': ['Read'],
                                   'lastSessionId': 'source-session'}}})
        put(self.pa / '.claude.json', {'oauthAccount': {'emailAddress': 'a@example.com'},
            'userID': 'a-user', 'modelAccessCache': {'a': True},
            'projects': {'/repo': {'lastSessionId': 'a-session'}}})
        put(self.pa / 'settings.json', {'theme': 'dark'})
        put(self.source / '.credentials.json', {'claudeAiOauth': {'token': 'SOURCE_SECRET'}})
        put(self.pa / '.credentials.json', {'claudeAiOauth': {'token': 'A_SECRET'}})
        (self.source / 'skills/tool').mkdir(parents=True)
        (self.source / 'skills/tool/SKILL.md').write_text('source skill')
        (self.pa / 'skills/own').mkdir(parents=True)
        (self.pa / 'skills/own/SKILL.md').write_text('preserve in backup')
        (self.source / 'CLAUDE.md').write_text('@RTK.md\n')
        (self.source / 'RTK.md').write_text('custom instructions')
        put(self.source / 'keybindings.json', {'bindings': [{'key': 'ctrl+k'}]})
        result = self.sync()
        self.assertEqual(get(self.pa / 'settings.json')['model'], 'opus')
        self.assertEqual(get(self.pa / 'settings.json')['theme'], 'light')
        state = get(self.pa / '.claude.json')
        self.assertEqual(state['oauthAccount']['emailAddress'], 'a@example.com')
        self.assertEqual(state['userID'], 'a-user')
        self.assertEqual(state['modelAccessCache'], {'a': True})
        self.assertNotIn('primaryApiKey', state)
        self.assertTrue(state['hasCompletedOnboarding'])
        self.assertTrue(state['projects']['/repo']['hasTrustDialogAccepted'])
        self.assertEqual(state['projects']['/repo']['lastSessionId'], 'a-session')
        self.assertEqual(state['mcpServers'], {'local': {'command': 'server'}})
        self.assertEqual(get(self.pa / '.credentials.json')['claudeAiOauth']['token'], 'A_SECRET')
        self.assertTrue((self.pa / 'skills').is_symlink())
        self.assertEqual((self.pb / 'skills/tool/SKILL.md').read_text(), 'source skill')
        self.assertEqual((self.pa / 'RTK.md').read_text(), 'custom instructions')
        self.assertEqual(get(self.pa / 'keybindings.json'), {'bindings': [{'key': 'ctrl+k'}]})
        backups = list(Path(result['backup']).rglob('SKILL.md'))
        self.assertTrue(any(p.read_text() == 'preserve in backup' for p in backups))
        self.assertNotIn('SOURCE_SECRET', (self.store.root / 'settings-sync.json').read_text())

    def test_profile_edits_propagate_after_atomic_replace_and_preserve_unrelated_changes(self):
        put(self.source / 'settings.json', {'model': 'opus', 'permissions': {'allow': ['Read'], 'deny': []}})
        self.sync()
        put(self.pa / 'replacement', {'model': 'sonnet', 'permissions': {'allow': ['Read'], 'deny': []}})
        (self.pa / 'replacement').replace(self.pa / 'settings.json')
        put(self.pb / 'settings.json', {'model': 'opus', 'permissions': {'allow': ['Read'], 'deny': ['Bash']}})
        self.sync()
        expected = {'model': 'sonnet', 'permissions': {'allow': ['Read'], 'deny': ['Bash']}}
        for p in (self.source, self.pa, self.pb):
            self.assertEqual(get(p / 'settings.json'), expected)
        self.assertIsNone(self.sync()['backup'])

    def test_deletions_and_new_profiles_follow_current_common_settings(self):
        put(self.source / 'settings.json', {'theme': 'light', 'effortLevel': 'high'})
        self.sync()
        put(self.pa / 'settings.json', {'theme': 'dark'})
        self.sync()
        c = self.store.create('C')
        self.sync()
        self.assertEqual(get(self.store.profile(c['id']) / 'settings.json'), {'theme': 'dark'})
        self.assertEqual(get(self.source / 'settings.json'), {'theme': 'dark'})

    def test_conflicting_edits_fail_without_rewriting_any_settings(self):
        put(self.source / 'settings.json', {'model': 'opus'})
        self.sync()
        put(self.pa / 'settings.json', {'model': 'sonnet'})
        put(self.pb / 'settings.json', {'model': 'haiku'})
        before = [(p / 'settings.json').read_bytes() for p in (self.source, self.pa, self.pb)]
        with self.assertRaises(AccountError):
            self.sync()
        self.assertEqual(before, [(p / 'settings.json').read_bytes() for p in (self.source, self.pa, self.pb)])

    def test_private_account_and_session_data_stays_private_during_later_syncs(self):
        put(self.source_state, {'theme': 'light', 'oauthAccount': {'email': 'source'}})
        put(self.pa / '.claude.json', {'oauthAccount': {'email': 'A'}, 'userID': 'A'})
        self.sync()
        data = get(self.pa / '.claude.json')
        data.update(theme='dark', oauthAccount={'email': 'A-new'}, userID='A-new',
                    someFutureAccountCache='private')
        put(self.pa / '.claude.json', data)
        self.sync()
        self.assertEqual(get(self.source_state)['oauthAccount'], {'email': 'source'})
        self.assertEqual(get(self.pb / '.claude.json')['theme'], 'dark')
        self.assertNotIn('oauthAccount', get(self.pb / '.claude.json'))
        self.assertNotIn('someFutureAccountCache', get(self.pb / '.claude.json'))
        self.assertEqual(get(self.pa / '.claude.json')['userID'], 'A-new')

    def test_invalid_json_held_claude_lock_and_foreign_symlink_fail_closed(self):
        put(self.source / 'settings.json', {'theme': 'light'})
        put(self.pa / '.claude.json', {'oauthAccount': {'email': 'A'}})
        before = (self.pa / '.claude.json').read_bytes()
        lock = self.pa / '.claude.json.lock'
        lock.mkdir()
        with self.assertRaises(AccountError):
            self.sync()
        self.assertTrue(lock.is_dir())
        self.assertEqual((self.pa / '.claude.json').read_bytes(), before)
        lock.rmdir()
        (self.source / 'settings.json').write_text('{')
        with self.assertRaises(AccountError):
            self.sync()
        put(self.source / 'settings.json', {'theme': 'light'})
        (self.pa / 'skills').symlink_to(self.home)
        with self.assertRaises(AccountError):
            self.sync()
        self.assertEqual((self.pa / '.claude.json').read_bytes(), before)

    def test_auth_overrides_in_shared_settings_are_not_distributed(self):
        put(self.source / 'settings.json', {'env': {'ANTHROPIC_API_KEY': 'SECRET'}})
        with self.assertRaises(AccountError) as ctx:
            self.sync()
        self.assertNotIn('SECRET', str(ctx.exception))
        self.assertFalse((self.pa / 'settings.json').exists())

    def test_initial_base_wins_structural_conflicts(self):
        put(self.source / 'settings.json', {'permissions': {}, 'modelSettings': {'effort': 'high'}})
        put(self.pa / 'settings.json', {'permissions': {'allow': ['Bash']}, 'modelSettings': False})
        self.sync()
        self.assertEqual(get(self.pa / 'settings.json'), get(self.source / 'settings.json'))
        self.assertEqual(get(self.pa / 'settings.json')['permissions'], {})

    def test_canonical_state_lock_blocks_alias_and_stale_locks_recover(self):
        target = self.home / 'actual-state.json'
        put(target, {'theme': 'light'})
        self.source_state.symlink_to(target)
        put(self.pa / '.claude.json', {'theme': 'dark'})
        lock = Path(str(target) + '.lock')
        lock.mkdir()
        with self.assertRaises(AccountError):
            self.sync()
        self.assertEqual(get(self.pa / '.claude.json')['theme'], 'dark')
        stale = time.time() - 120
        os.utime(lock, (stale, stale))
        self.sync()
        self.assertFalse(lock.exists())
        self.assertEqual(get(self.pa / '.claude.json')['theme'], 'light')
        self.assertTrue(self.source_state.is_symlink())


if __name__ == '__main__':
    unittest.main()

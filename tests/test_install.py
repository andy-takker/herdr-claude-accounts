import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from herdr_claude_accounts.accounts import Store, AccountError
from herdr_claude_accounts.shell_install import install, uninstall


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home / 'data')
        self.rc = self.home / '.zshrc'
        self.original = '# my config\nexport PATH=/my/bin:$PATH\nfish\n'
        self.rc.write_text(self.original)
        self.rc.chmod(0o640)

    def test_install_is_idempotent_preserves_config_and_runs_entrypoints(self):
        result = install(self.store, '/bin/echo', home=self.home)
        text = self.rc.read_text()
        self.assertLess(text.index('# BEGIN Claude Accounts'), text.index('\nfish\n'))
        self.assertEqual(self.rc.stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.store.root / 'zshrc.backup').read_text(), self.original)
        install(self.store, '/bin/echo', home=self.home)
        self.assertEqual(self.rc.read_text(), text)
        self.assertEqual((self.store.root / 'zshrc.backup').read_text(), self.original)
        p = subprocess.run([str(self.store.root / 'bin/claude'), 'hello world'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), 'hello world')
        p = subprocess.run([str(self.store.root / 'bin/claude-accounts'), 'list'], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('selected', p.stdout)
        self.assertTrue(Path(result['fish_config']).exists())

    def test_uninstall_preserves_edits_outside_block_and_all_profiles(self):
        a = self.store.create('Work')
        install(self.store, '/bin/echo', home=self.home)
        self.rc.write_text(self.rc.read_text() + '# added later\n')
        uninstall(self.store, home=self.home)
        self.assertEqual(self.rc.read_text(), self.original + '# added later\n')
        self.assertTrue(self.store.profile(a['id']).is_dir())
        self.assertFalse((self.home / '.config/fish/conf.d/zzz-herdr-claude-accounts.fish').exists())

    def test_user_alias_or_function_is_not_silently_overridden(self):
        for content in ("alias claude='elsewhere'\n", 'function claude() { echo hi; }\n',
                        'claude() { echo hi; }\n'):
            self.rc.write_text(content)
            with self.assertRaises(AccountError):
                install(self.store, '/bin/echo', home=self.home)
            self.assertEqual(self.rc.read_text(), content)

    def test_fish_existing_user_file_and_symlinked_rc_are_preserved(self):
        fish = self.home / '.config/fish/conf.d/zzz-herdr-claude-accounts.fish'
        fish.parent.mkdir(parents=True)
        fish.write_text('# unrelated user file\n')
        with self.assertRaises(AccountError):
            install(self.store, '/bin/echo', home=self.home)
        self.assertEqual(fish.read_text(), '# unrelated user file\n')
        self.assertEqual(self.rc.read_text(), self.original)
        self.rc.unlink()
        self.rc.symlink_to(fish)
        with self.assertRaises(AccountError):
            install(self.store, '/bin/echo', home=self.home)


if __name__ == '__main__':
    unittest.main()

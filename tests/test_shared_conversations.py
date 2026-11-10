import json
import os
from pathlib import Path
import tempfile
import unittest

from herdr_claude_accounts.accounts import AccountError, Store
from herdr_claude_accounts.shared_settings import synchronize
from herdr_claude_accounts.shared_conversations import prepare


class SharedConversationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "system"
        self.source.mkdir()
        self.store = Store(self.root / "accounts")
        self.a = self.store.create("A")
        self.b = self.store.create("B")
        self.pa = self.store.profile(self.a["id"])
        self.pb = self.store.profile(self.b["id"])

    def put(self, home, relative, data):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def sync(self):
        return synchronize(self.store, self.source, self.source / ".claude.json", share_conversations=True)

    def test_existing_context_unites_and_new_writes_reach_every_account(self):
        self.put(self.source, "projects/repo/system.jsonl", b'{"uuid":"system"}\n')
        self.put(self.pa, "projects/repo/a.jsonl", b'{"uuid":"a"}\n')
        self.put(self.pb, "projects/repo/b/subagents/agent.jsonl", b'{"uuid":"agent"}\n')
        self.put(self.pa, "projects/repo/memory/MEMORY.md", b"project facts")
        self.put(self.pa, "file-history/a/file@v1", b"checkpoint")
        self.put(self.pb, "tasks/b/1.json", b'{"status":"pending"}')
        source_history = self.put(self.source, "history.jsonl", b'{"display":"system"}\n')
        self.put(self.pa, "history.jsonl", b'{"display":"system"}\n{"display":"a"}\n')
        self.put(self.pb, "history.jsonl", b'{"display":"b"}\n')
        self.put(self.pa, ".credentials.json", b"private-token")
        self.put(self.pa, "sessions/99999999.json", b"private runtime")
        inode = source_history.stat().st_ino
        result = self.sync()
        self.assertEqual(result["shared_profiles"], 2)
        self.assertEqual(result["pending_profiles"], [])
        self.assertEqual(source_history.stat().st_ino, inode)
        for home in (self.source, self.pa, self.pb):
            self.assertEqual((home / "projects/repo/a.jsonl").read_bytes(), b'{"uuid":"a"}\n')
            self.assertEqual((home / "projects/repo/memory/MEMORY.md").read_bytes(), b"project facts")
            self.assertEqual((home / "file-history/a/file@v1").read_bytes(), b"checkpoint")
            self.assertEqual((home / "tasks/b/1.json").read_bytes(), b'{"status":"pending"}')
            self.assertEqual(len((home / "history.jsonl").read_bytes().splitlines()), 3)
        self.assertEqual((self.pa / ".credentials.json").read_bytes(), b"private-token")
        self.assertFalse((self.pa / "sessions").is_symlink())
        self.put(self.pb, "projects/repo/new.jsonl", b"new")
        self.assertEqual((self.pa / "projects/repo/new.jsonl").read_bytes(), b"new")
        backups = list(Path(result["backup"]).rglob("a.jsonl"))
        self.assertTrue(backups)
        self.put(self.pa, "projects/repo/a.jsonl", b"changed")
        self.assertEqual(backups[0].read_bytes(), b'{"uuid":"a"}\n')
        self.assertIsNone(self.sync()["backup"])

    def test_live_profile_import_preserves_open_append_and_finishes_when_idle(self):
        session = self.put(self.pa, "sessions/" + str(os.getpid()) + ".json", b'{}')
        transcript = self.put(self.pa, "projects/repo/a.jsonl", b'{"n":1}\n')
        self.put(self.pa, "history.jsonl", b'{"n":1}\n')
        with transcript.open("ab") as writer:
            result = self.sync()
            self.assertIn(self.a["id"], result["pending_profiles"])
            self.assertFalse((self.pa / "projects").is_symlink())
            self.assertEqual(transcript.stat().st_ino, (self.source / "projects/repo/a.jsonl").stat().st_ino)
            writer.write(b'{"n":2}\n')
            writer.flush()
            self.assertEqual((self.pb / "projects/repo/a.jsonl").read_bytes(), b'{"n":1}\n{"n":2}\n')
        self.put(self.pa, "projects/repo/new.jsonl", b'{"n":3}\n')
        with (self.pa / "history.jsonl").open("ab") as history:
            history.write(b'{"n":2}\n')
        self.sync()
        self.assertTrue((self.source / "projects/repo/new.jsonl").exists())
        self.assertEqual((self.source / "history.jsonl").read_bytes(), b'{"n":1}\n{"n":2}\n')
        session.unlink()
        result = self.sync()
        self.assertEqual(result["pending_profiles"], [])
        self.assertTrue((self.pa / "projects").is_symlink())
        self.assertEqual((self.pa / "projects/repo/a.jsonl").read_bytes(), b'{"n":1}\n{"n":2}\n')

    def test_divergent_transcript_fails_before_any_context_or_settings_mutation(self):
        original = self.put(self.source, "projects/repo/same.jsonl", b'{"n":1}\n')
        divergent = self.put(self.pa, "projects/repo/same.jsonl", b'{"n":2}\n')
        self.put(self.pb, "projects/repo/unique.jsonl", b'{"n":3}\n')
        self.put(self.source, "settings.json", b'{"model":"opus"}')
        with self.assertRaises(AccountError):
            self.sync()
        self.assertEqual(original.read_bytes(), b'{"n":1}\n')
        self.assertEqual(divergent.read_bytes(), b'{"n":2}\n')
        self.assertFalse((self.source / "projects/repo/unique.jsonl").exists())
        self.assertFalse((self.pa / "settings.json").exists())

    def test_append_only_transcript_copies_keep_longest_and_back_up_originals(self):
        self.put(self.source, "projects/repo/same.jsonl", b'{"n":1}\n')
        self.put(self.pa, "projects/repo/same.jsonl", b'{"n":1}\n{"n":2}\n')
        self.sync()
        self.assertEqual((self.pb / "projects/repo/same.jsonl").read_bytes(), b'{"n":1}\n{"n":2}\n')

    def test_merge_does_not_mutate_shorter_profile_backup(self):
        self.put(self.pa, "projects/repo/same.jsonl", b'{"n":1}\n')
        self.put(self.pb, "projects/repo/same.jsonl", b'{"n":1}\n{"n":2}\n')
        result = self.sync()
        backup = Path(result['backup']) / (self.a['id'] + '-context/projects/repo/same.jsonl')
        self.assertEqual(backup.read_bytes(), b'{"n":1}\n')

    def test_invalid_history_and_divergent_memory_leave_original_paths(self):
        self.put(self.source, 'history.jsonl', b'{"n":1}\n')
        history = self.put(self.pa, 'history.jsonl', b'{"n":2}')
        with self.assertRaises(AccountError):
            self.sync()
        self.assertFalse((self.pa / 'projects').exists())
        history.write_bytes(b'{"n":2}\n')
        self.put(self.source, 'projects/repo/memory/MEMORY.md', b'source facts')
        self.put(self.pa, 'projects/repo/memory/MEMORY.md', b'account facts')
        with self.assertRaises(AccountError):
            self.sync()
        self.assertFalse((self.pa / 'projects').is_symlink())
        self.assertEqual((self.source / 'history.jsonl').read_bytes(), b'{"n":1}\n')

    def test_shared_noop_does_not_parse_history_while_native_writer_is_appending(self):
        self.sync()
        self.put(self.source, 'history.jsonl', b'{"unfinished":')
        result = self.sync()
        self.assertIsNone(result['backup'])
        self.assertEqual((self.source / 'history.jsonl').read_bytes(), b'{"unfinished":')

    def test_missing_shared_tasks_directory_is_recreated_without_replacing_links(self):
        self.put(self.source, 'projects/repo/saved.jsonl', b'{"n":1}\n')
        self.sync()
        links = [(home / 'tasks').lstat().st_ino for home in (self.pa, self.pb)]
        (self.source / 'tasks').rmdir()
        result = self.sync()
        self.assertEqual(result['pending_profiles'], [])
        self.assertIsNone(result['backup'])
        self.assertTrue((self.source / 'tasks').is_dir())
        self.assertEqual([(home / 'tasks').lstat().st_ino for home in (self.pa, self.pb)], links)
        self.put(self.pa, 'tasks/session/1.json', b'{"status":"pending"}')
        self.assertEqual((self.pb / 'tasks/session/1.json').read_bytes(), b'{"status":"pending"}')
        self.assertEqual((self.pb / 'projects/repo/saved.jsonl').read_bytes(), b'{"n":1}\n')

    def test_missing_shared_target_does_not_allow_foreign_link_or_file(self):
        self.sync()
        target = self.source / 'tasks'
        target.rmdir()
        link = self.pa / 'tasks'
        link.unlink()
        link.symlink_to(self.root / 'foreign-missing')
        with self.assertRaises(AccountError):
            self.sync()
        self.assertFalse(target.exists())
        link.unlink()
        link.symlink_to(target)
        target.write_bytes(b'not a directory')
        with self.assertRaises(AccountError):
            self.sync()
        self.assertEqual(target.read_bytes(), b'not a directory')

    def test_interrupted_cutover_keeps_staged_original_for_recovery(self):
        original = self.put(self.pa, '.sharing-projects/repo/a.jsonl', b'{"n":1}\n')
        with self.assertRaises(AccountError):
            self.sync()
        self.assertEqual(original.read_bytes(), b'{"n":1}\n')
        self.assertFalse((self.pa / 'projects').exists())

    def test_system_collision_after_preparation_preserves_original_profile(self):
        original = self.put(self.pa, 'projects/repo/a.jsonl', b'{"branch":"account"}\n')

        def backup(path, label):
            target = self.root / 'backup' / label / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            return target

        apply = prepare([self.source, self.pa, self.pb], backup)
        collision = self.put(self.source, 'projects/repo/a.jsonl', b'{"branch":"system"}\n')
        with self.assertRaises(AccountError):
            apply()
        self.assertFalse((self.pa / 'projects').is_symlink())
        self.assertEqual(original.read_bytes(), b'{"branch":"account"}\n')
        self.assertEqual(collision.read_bytes(), b'{"branch":"system"}\n')

    def test_new_profile_context_after_preparation_is_not_hidden_by_cutover(self):
        self.put(self.pa, 'projects/a.jsonl', b'{"n":1}\n')

        def backup(path, label):
            target = self.root / 'backup' / label / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            return target

        apply = prepare([self.source, self.pa, self.pb], backup)
        late = self.put(self.pa, 'projects/new.jsonl', b'{"n":2}\n')
        with self.assertRaises(AccountError):
            apply()
        self.assertFalse((self.pa / 'projects').is_symlink())
        self.assertEqual(late.read_bytes(), b'{"n":2}\n')

    def test_native_session_start_during_backup_preserves_open_history(self):
        history = self.put(self.pa, 'history.jsonl', b'{"n":1}\n')
        writers = []

        def backup(path, label):
            target = self.root / 'backup' / label / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not writers:
                self.put(self.pa, 'sessions/' + str(os.getpid()) + '.json', b'{}')
                writers.append(history.open('ab'))
                self.addCleanup(writers[0].close)
            return target

        apply = prepare([self.source, self.pa, self.pb], backup)
        with self.assertRaises(AccountError):
            apply()
        writers[0].write(b'{"n":2}\n')
        writers[0].flush()
        self.assertGreater(os.fstat(writers[0].fileno()).st_nlink, 0)
        self.assertFalse(history.is_symlink())
        self.assertEqual(history.read_bytes(), b'{"n":1}\n{"n":2}\n')

    def test_settings_sync_does_not_migrate_conversations_without_opt_in(self):
        self.put(self.pa, 'projects/repo/a.jsonl', b'{"n":1}\n')
        result = synchronize(self.store, self.source, self.source / '.claude.json')
        self.assertFalse((self.pa / 'projects').is_symlink())
        self.assertFalse((self.source / 'projects/repo/a.jsonl').exists())
        self.assertNotIn('shared_profiles', result)

    def test_foreign_links_and_nonregular_data_fail_without_following(self):
        external = self.put(self.root, "external", b"private")
        (self.pa / "projects").mkdir()
        (self.pa / "projects/link").symlink_to(external)
        with self.assertRaises(AccountError):
            self.sync()
        self.assertEqual(external.read_bytes(), b"private")
        self.assertFalse((self.pb / "projects").exists())


if __name__ == "__main__":
    unittest.main()

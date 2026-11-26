import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spoty.storage.contracts import StorageError
from spoty.storage.git_backup import backup_data_repository


@unittest.skipUnless(shutil.which("git"), "Git required for offline backup tests")
class GitBackupTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.remote = self.root / "source.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        self.worktree = self.root / "work"
        subprocess.run(["git", "clone", str(self.remote), str(self.worktree)], check=True, capture_output=True)
        self.git("-c", "user.name=Backup test", "-c", "user.email=backup@example.invalid",
                 "commit", "--allow-empty", "-m", "Initial")
        self.git("push", "origin", "HEAD:main")
        self.backup = self.root / "offline" / "spoty-data.git"
        (self.root / "offline").mkdir()

    def git(self, *arguments):
        return subprocess.run(
            ["git", "-C", str(self.worktree), *arguments], check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def commit_file(self, name, data):
        path = self.worktree / name
        path.write_text(data, encoding="utf-8")
        self.git("add", name)
        self.git("-c", "user.name=Backup test", "-c", "user.email=backup@example.invalid",
                 "commit", "-m", name)
        self.git("push", "origin", "HEAD:main")
        return self.git("rev-parse", "HEAD")

    def bare_git(self, *arguments):
        return subprocess.run(
            ["git", "--git-dir", str(self.backup), *arguments], check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def test_initial_and_incremental_backup_has_immutable_checkpoint_refs(self):
        first = backup_data_repository(self.backup, source=str(self.remote))
        self.assertEqual(first.remote_commit, first.checkpoint_commit)
        self.assertFalse(first.advanced)
        first_head = first.remote_commit
        second_commit = self.commit_file("listening.csv", "synthetic\n")
        second = backup_data_repository(self.backup, source=str(self.remote))
        self.assertEqual(second.checkpoint_commit, first_head)
        self.assertEqual(second.remote_commit, second_commit)
        self.assertTrue(second.advanced)
        checkpoint = self.bare_git("rev-parse", second.checkpoint_ref)
        self.assertEqual(checkpoint, first_head)
        self.assertFalse(self._path_exists(second.checkpoint_ref, "listening.csv"))
        self.assertEqual(self.bare_git("config", "remote.origin.mirror"), "false")
        self.assertEqual(self.bare_git("config", "push.default"), "nothing")
        refs = self.bare_git("for-each-ref", "--format=%(refname)", "refs/backup/checkpoints")
        self.assertEqual(len(refs.splitlines()), 2)

    def _path_exists(self, ref, path):
        result = subprocess.run(
            ["git", "--git-dir", str(self.backup), "cat-file", "-e", f"{ref}:{path}"],
            capture_output=True,
        )
        return result.returncode == 0

    def test_remote_force_rewind_keeps_previous_tip_recoverable(self):
        first_backup = backup_data_repository(self.backup, source=str(self.remote))
        old_tip = self.commit_file("old.csv", "preserve\n")
        second_backup = backup_data_repository(self.backup, source=str(self.remote))
        self.assertEqual(second_backup.remote_commit, old_tip)
        rewound_to = self.git("rev-parse", "HEAD~1")
        subprocess.run(
            ["git", "--git-dir", str(self.remote), "update-ref", "refs/heads/main", rewound_to],
            check=True, capture_output=True,
        )
        result = backup_data_repository(self.backup, source=str(self.remote))
        self.assertTrue(result.advanced)
        self.assertEqual(result.remote_commit, rewound_to)
        self.assertEqual(self.bare_git("show", f"{result.checkpoint_ref}:old.csv"), "preserve")
        self.assertNotEqual(old_tip, result.remote_commit)
        self.assertEqual(self.bare_git("rev-parse", first_backup.checkpoint_ref), first_backup.remote_commit)

    def test_deleted_remote_branch_fails_without_losing_local_backup(self):
        result = backup_data_repository(self.backup, source=str(self.remote))
        subprocess.run(
            ["git", "--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/main"],
            check=True, capture_output=True,
        )
        with self.assertRaises(StorageError):
            backup_data_repository(self.backup, source=str(self.remote))
        self.assertEqual(self.bare_git("rev-parse", result.checkpoint_ref), result.remote_commit)

    def test_bad_destination_and_credential_bearing_remote_rejected(self):
        existing = self.root / "not-bare"
        existing.mkdir()
        (existing / "data.txt").write_text("do not modify", encoding="utf-8")
        with self.assertRaises(StorageError):
            backup_data_repository(existing, source=str(self.remote))
        repository_root = Path(__file__).resolve().parents[1]
        with self.assertRaises(StorageError):
            backup_data_repository(repository_root / "backup", source=str(self.remote))
        with self.assertRaises(StorageError):
            backup_data_repository(repository_root.parent, source=str(self.remote))
        self.assertEqual((existing / "data.txt").read_text(encoding="utf-8"), "do not modify")
        for source in (
            "https://user:token@github.com/owner/data.git",
            "http://github.com/owner/data.git",
            "https://example.invalid/owner/data.git",
        ):
            with self.subTest(source=source), self.assertRaises(StorageError):
                backup_data_repository(self.root / "other.git", source=source)

    def test_wrong_source_for_existing_backup_is_rejected(self):
        backup_data_repository(self.backup, source=str(self.remote))
        different = self.root / "other.git"
        subprocess.run(["git", "init", "--bare", str(different)], check=True, capture_output=True)
        with self.assertRaises(StorageError):
            backup_data_repository(self.backup, source=str(different))

    def test_remote_change_during_fetch_keeps_pre_fetch_checkpoint(self):
        result = backup_data_repository(self.backup, source=str(self.remote))
        new_tip = self.commit_file("new.csv", "new\n")
        from spoty.storage.git_backup import _git_dir as original_fetch
        changed = False

        def advance_after_fetch(path, arguments, *, capture=True):
            nonlocal changed
            result_value = original_fetch(path, arguments, capture=capture)
            if arguments[:1] == ["fetch"] and not changed:
                changed = True
                self.commit_file("race.csv", "raced\n")
            return result_value

        with patch("spoty.storage.git_backup._git_dir", side_effect=advance_after_fetch):
            with self.assertRaises(StorageError):
                backup_data_repository(self.backup, source=str(self.remote))
        self.assertEqual(self.bare_git("rev-parse", result.checkpoint_ref), result.remote_commit)
        self.assertEqual(self.bare_git("rev-parse", "refs/heads/main"), new_tip)

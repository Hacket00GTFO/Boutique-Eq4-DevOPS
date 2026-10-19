"""Exercise real merges/pushes against disposable local bare repositories."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'sync.py'


class BranchSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='boutique-sync-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'working'
        self.remote = self.root / 'remote.git'
        self.summary = self.root / 'summary.md'
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        GITHUB_STEP_SUMMARY=str(self.summary), GIT_TERMINAL_PROMPT='0')
        self.git('init', '--bare', '--initial-branch=main', str(self.remote), cwd=self.root)
        self.git('init', '--initial-branch=main', str(self.repo), cwd=self.root)
        self.git('config', 'user.name', 'Sync test')
        self.git('config', 'user.email', 'sync@example.invalid')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.commit('common.txt', 'base\n')
        self.git('switch', '-c', 'development')
        self.commit('shared.txt', 'promoted to main\n')
        for branch in ('David', 'Norberto', 'extra'):
            self.git('branch', branch)
        self.git('switch', 'main')
        self.git('merge', '--ff-only', 'development')
        self.git('switch', 'development')
        self.commit('development.txt', 'new development work\n')
        self.git('switch', 'David')
        self.commit('david.txt', 'David own work\n')
        self.git('switch', 'Norberto')
        self.commit('norberto.txt', 'Norberto own work\n')
        self.git('switch', 'development')
        self.git('push', 'origin', 'main', 'development', 'David', 'Norberto', 'extra')
        self.original = {branch: self.remote_sha(branch) for branch in ('main', 'development', 'David', 'Norberto', 'extra')}

    def git(self, *args, cwd=None, check=True):
        result = subprocess.run(['git', *args], cwd=cwd or self.repo, env=self.env,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        if check and result.returncode:
            self.fail(f'Fixture Git failed: {args}: {result.stderr}')
        return result

    def commit(self, name, text):
        (self.repo / name).write_text(text, encoding='utf-8')
        self.git('add', name)
        self.git('commit', '-m', 'Update ' + name)

    def remote_sha(self, branch):
        return self.git('--git-dir=' + str(self.remote), 'rev-parse', 'refs/heads/' + branch).stdout.strip()

    def run_sync(self, targets=('David', 'Norberto'), push=True):
        args = [sys.executable, str(SCRIPT)]
        for branch in targets:
            args.extend(['--target', branch])
        if push:
            args.append('--push')
        return subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=90)

    def assert_preserved(self):
        for branch in ('main', 'development', 'extra'):
            self.assertEqual(self.remote_sha(branch), self.original[branch])
        self.assertEqual(self.git('branch', '--show-current').stdout.strip(), 'development')
        self.assertEqual(self.git('worktree', 'list', '--porcelain').stdout.count('worktree '), 1)
        self.assertFalse(self.git('--git-dir=' + str(self.remote), 'for-each-ref', 'refs/heads/sync/').stdout)

    def assert_contains(self, branch, commit):
        self.assertEqual(self.git('--git-dir=' + str(self.remote), 'merge-base', '--is-ancestor',
                                  commit, self.remote_sha(branch), check=False).returncode, 0)

    def test_syncs_after_promotion_to_main_without_losing_branch_commits(self):
        base = self.git('merge-base', 'David', 'development').stdout.strip()
        self.assertEqual(self.git('merge-base', '--is-ancestor', base, 'main', check=False).returncode, 0)
        (self.repo / 'untracked.txt').write_text('local work', encoding='utf-8')
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for branch in ('David', 'Norberto'):
            self.assert_contains(branch, self.original['development'])
            self.assert_contains(branch, self.original[branch])
        self.assertIn('David: Synced', result.stdout)
        self.assertEqual((self.repo / 'untracked.txt').read_text(), 'local work')
        self.assert_preserved()

    def test_repeated_run_is_idempotent(self):
        self.assertEqual(self.run_sync().returncode, 0)
        before = {branch: self.remote_sha(branch) for branch in ('David', 'Norberto')}
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.count('Already up to date'), 2)
        for branch, commit in before.items():
            self.assertEqual(self.remote_sha(branch), commit)
        self.assert_preserved()

    def test_conflict_fails_job_preserves_destination_and_continues_other_branch(self):
        self.git('switch', 'David')
        self.commit('common.txt', 'David conflicting change\n')
        self.git('switch', 'development')
        self.commit('common.txt', 'development conflicting change\n')
        self.git('push', 'origin', 'David', 'development')
        david = self.remote_sha('David')
        source = self.remote_sha('development')
        result = self.run_sync()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('David: Conflict', result.stdout)
        self.assertIn('Norberto: Synced', result.stdout)
        self.assertEqual(self.remote_sha('David'), david)
        self.assert_contains('Norberto', source)
        self.assertIn('Fallos: 1', self.summary.read_text(encoding='utf-8'))
        self.assertEqual(self.git('worktree', 'list', '--porcelain').stdout.count('worktree '), 1)

    @unittest.skipIf(os.name == 'nt', 'POSIX server-side Git hook is exercised in Linux CI')
    def test_push_rejection_fails_job_without_overwriting_target(self):
        hook = self.remote / 'hooks' / 'update'
        hook.write_text('#!/bin/sh\n[ "$1" != "refs/heads/David" ]\n', encoding='utf-8')
        hook.chmod(0o755)
        result = self.run_sync()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('David: Push failed', result.stdout)
        self.assertIn('Norberto: Synced', result.stdout)
        self.assertEqual(self.remote_sha('David'), self.original['David'])
        self.assert_contains('Norberto', self.original['development'])
        self.assert_preserved()

    def test_missing_target_is_failure_and_is_not_created(self):
        self.git('push', 'origin', '--delete', 'David')
        result = self.run_sync()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('David: Failed', result.stdout)
        self.assertNotEqual(self.git('--git-dir=' + str(self.remote), 'show-ref', '--verify',
                                     'refs/heads/David', check=False).returncode, 0)
        self.assert_contains('Norberto', self.original['development'])
        self.assert_preserved()

    def test_preview_merges_without_pushing(self):
        result = self.run_sync(push=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.count('Preview'), 2)
        for branch in ('David', 'Norberto'):
            self.assertEqual(self.remote_sha(branch), self.original[branch])
        self.assert_preserved()

    def test_protected_destinations_are_rejected(self):
        for branch in ('main', 'development', 'release/stable', 'sync/temporary'):
            with self.subTest(branch=branch):
                self.assertNotEqual(self.run_sync(targets=(branch,)).returncode, 0)
        self.assert_preserved()

    def test_unrelated_history_is_not_merged(self):
        self.git('switch', '--orphan', 'unrelated')
        self.commit('unrelated.txt', 'independent history\n')
        self.git('push', 'origin', '--delete', 'David')
        self.git('push', 'origin', 'HEAD:David')
        david = self.remote_sha('David')
        self.git('switch', 'development')
        result = self.run_sync()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('no comparten historial', result.stdout)
        self.assertEqual(self.remote_sha('David'), david)
        self.assert_contains('Norberto', self.original['development'])
        self.assert_preserved()


if __name__ == '__main__':
    unittest.main()

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_event import authorize

SHA = 'a' * 40
REPO = 'owner/repo'
PR = {'merged_at': '2026-10-20T00:00:00Z', 'merge_commit_sha': SHA,
      'base': {'ref': 'main'}, 'head': {'ref': 'development', 'repo': {'full_name': REPO}}}


class ReleaseEventTests(unittest.TestCase):
    def test_main_push_for_exact_development_merge_is_authorized(self):
        self.assertTrue(authorize('push', 'refs/heads/main', SHA, REPO, {}, lambda *_: [PR])[0])

    def test_other_branch_fork_unmerged_and_unrelated_sha_are_rejected(self):
        cases = []
        for field, value in [('merged_at', None), ('merge_commit_sha', 'b' * 40)]:
            case = copy.deepcopy(PR)
            case[field] = value
            cases.append(case)
        case = copy.deepcopy(PR)
        case['head']['ref'] = 'another-branch'
        cases.append(case)
        case = copy.deepcopy(PR)
        case['head']['repo']['full_name'] = 'another/repo'
        cases.append(case)
        case = copy.deepcopy(PR)
        case['head']['repo'] = None
        cases.append(case)
        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assertFalse(authorize('push', 'refs/heads/main', SHA, REPO, {}, lambda *_: [candidate])[0])

    def test_a_direct_main_push_without_a_pr_does_not_deploy(self):
        self.assertFalse(authorize('push', 'refs/heads/main', SHA, REPO, {}, lambda *_: [])[0])

    def test_manual_main_release_and_development_inventory_are_separate(self):
        def forbidden(*_):
            raise AssertionError('Manual runs do not query PR metadata.')
        self.assertTrue(authorize('workflow_dispatch', 'refs/heads/main', SHA, REPO,
                                  {'inputs': {'operation': 'release'}}, forbidden)[0])
        self.assertFalse(authorize('workflow_dispatch', 'refs/heads/development', SHA, REPO, {}, forbidden)[0])
        for operation in ('inventory', 'initialize-local-data', 'prepare-vault-kms', 'rebuild-infrastructure', ''):
            self.assertFalse(authorize('workflow_dispatch', 'refs/heads/main', SHA, REPO,
                                       {'inputs': {'operation': operation}}, forbidden)[0])

    def test_deleted_main_and_unknown_events_cannot_release(self):
        self.assertFalse(authorize('push', 'refs/heads/main', SHA, REPO, {'deleted': True}, lambda *_: [PR])[0])
        self.assertFalse(authorize('pull_request', 'refs/heads/main', SHA, REPO, {}, lambda *_: [PR])[0])

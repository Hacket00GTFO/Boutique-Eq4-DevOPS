import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import SafeError
from github_oidc import role_configuration, validate_existing_role


class ImmutableSubjectTests(unittest.TestCase):
    def test_subject_matches_the_actual_repository_immutable_template(self):
        role = role_configuration('Hacket00GTFO/Boutique-Eq4-DevOPS', 1333737803, 181441407)
        self.assertEqual(role['bound_subject'],
                         'repo:Hacket00GTFO@181441407/Boutique-Eq4-DevOPS@1333737803:environment:vault-lab')

    def test_only_same_immutable_ids_allow_updating_a_legacy_role(self):
        expected = role_configuration('owner/repo', 123, 456)
        legacy = dict(expected, bound_subject='repo:owner/repo:environment:vault-lab')
        validate_existing_role(legacy, expected, 'owner/repo')
        validate_existing_role(expected, expected, 'owner/repo')
        validate_existing_role({}, expected, 'owner/repo')
        for key in ('repository_id', 'repository_owner_id'):
            changed = copy.deepcopy(legacy)
            changed['bound_claims'][key] = '999'
            with self.subTest(key=key), self.assertRaises(SafeError):
                validate_existing_role(changed, expected, 'owner/repo')
        for subject in ('repo:other/repo:environment:vault-lab', 'repo:owner/repo:ref:refs/heads/main', ''):
            with self.subTest(subject=subject), self.assertRaises(SafeError):
                validate_existing_role(dict(legacy, bound_subject=subject), expected, 'owner/repo')

    def test_extra_claim_restrictions_are_preserved_by_refusing_replacement(self):
        expected = role_configuration('owner/repo', 123, 456)
        restricted = copy.deepcopy(expected)
        restricted['bound_claims']['actor_id'] = '789'
        with self.assertRaises(SafeError):
            validate_existing_role(restricted, expected, 'owner/repo')

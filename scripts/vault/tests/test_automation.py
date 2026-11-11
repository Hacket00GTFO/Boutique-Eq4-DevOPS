"""Resource boundaries and credential handling for the automatic Lab release."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import SafeError
from github_oidc import configure, role_configuration
import resume_lab


class ExistingLab:
    def __init__(self, bad_target=False, bad_state=False):
        self.calls = []
        self.bad_target, self.bad_state = bad_target, bad_state

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('eks', 'describe-cluster'):
            return {'cluster': {'status': 'ACTIVE', 'resourcesVpcConfig': {'vpcId': 'vpc-owned'}}}
        if args[:2] == ('eks', 'describe-nodegroup'):
            return {'nodegroup': {'resources': {'autoScalingGroups': [{'name': args[-1]}]}}}
        if args[0] == 'autoscaling':
            return {'AutoScalingGroups': [{'Instances': [{'InstanceId': 'i-11111111'}]}]}
        if args[:2] == ('ec2', 'describe-instances'):
            return {'Reservations': [{'Instances': [
                {'InstanceId': 'i-22222222', 'VpcId': 'vpc-other' if self.bad_target else 'vpc-owned',
                 'Tags': [{'Key': 'Name', 'Value': 'boutique-vault-lab'}],
                 'State': {'Name': 'terminated' if self.bad_state else 'stopped'}},
                {'InstanceId': 'i-11111111', 'State': {'Name': 'running'}},
            ]}]}
        if args[0] == 'ssm':
            return {'InstanceInformationList': [{'InstanceId': 'i-22222222', 'PingStatus': 'Online'}]}
        return {}


class AutomationTests(unittest.TestCase):
    def test_resume_starts_only_stopped_members_of_this_deployment(self):
        lab = ExistingLab()
        resume_lab.resume('i-22222222', call=lab, sleep=lambda _: None)
        starts = [c for c in lab.calls if c[:2] == ('ec2', 'start-instances')]
        self.assertEqual(starts, [('ec2', 'start-instances', '--instance-ids', 'i-22222222')])
        self.assertFalse(any('create' in word or 'delete' in word or 'terminate' in word
                             for c in lab.calls for word in c))

    def test_wrong_vpc_or_terminated_target_fails_before_mutations(self):
        for settings in ({'bad_target': True}, {'bad_state': True}):
            with self.subTest(settings=settings):
                lab = ExistingLab(**settings)
                with self.assertRaises(SafeError):
                    resume_lab.resume('i-22222222', call=lab)
                self.assertFalse(any(c[:2] == ('ec2', 'start-instances') for c in lab.calls))

    def test_cancelled_credentials_diagnostic_does_not_echo_aws_response(self):
        result = subprocess.CompletedProcess([], 1, '', 'voc-cancel-cred isolated-secret-fixture')
        with patch.object(resume_lab.subprocess, 'run', return_value=result):
            with self.assertRaises(SafeError) as failure:
                resume_lab.aws('eks', 'describe-cluster')
        self.assertIn('Lab is inactive', str(failure.exception))
        self.assertNotIn('isolated-secret-fixture', str(failure.exception))

    def test_missing_resources_are_not_misdiagnosed_as_expired_credentials(self):
        for service, code in (('eks', 'ResourceNotFoundException'),
                              ('ec2', 'InvalidInstanceID.NotFound')):
            result = subprocess.CompletedProcess([], 1, '', code + ' isolated-secret-fixture')
            with self.subTest(service=service), patch.object(resume_lab.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(SafeError, 'missing') as failure:
                    resume_lab.aws(service, 'describe')
                self.assertNotIn('isolated-secret-fixture', str(failure.exception))
                self.assertNotIn('expired', str(failure.exception))

    def test_github_trust_binds_numeric_ownership_main_environment_and_workflow(self):
        role = role_configuration('owner/repo', 123, 456)
        self.assertEqual(role['bound_subject'], 'repo:owner@456/repo@123:environment:vault-lab')
        self.assertEqual(role['bound_claims']['ref'], 'refs/heads/main')
        self.assertEqual(role['bound_claims']['repository_id'], '123')
        self.assertEqual(role['bound_claims']['repository_owner_id'], '456')
        self.assertTrue(all(v.endswith('@refs/heads/main') and '*' not in v
                            for v in role['bound_claims']['workflow_ref']))
        self.assertEqual(role['token_policies'], ['boutique-ci'])
        self.assertTrue(role['token_no_default_policy'])
        self.assertEqual(role['token_type'], 'batch')
        self.assertEqual(role['token_explicit_max_ttl'], 900)

    def test_repository_and_numeric_ids_cannot_inject_trust_patterns(self):
        for repo, ident, owner in [('owner/*', 123, 456), ('owner/repo', '*', 456),
                                   ('owner/repo', 123, '0')]:
            with self.subTest(repo=repo, ident=ident, owner=owner), self.assertRaises(SafeError):
                role_configuration(repo, ident, owner)

    def test_existing_foreign_jwt_trust_is_preserved_before_writes(self):
        for config in ({'bound_issuer': 'https://another.example'},
                       {'oidc_discovery_url': 'https://another.example'},
                       {'jwks_url': 'https://another.example/keys'},
                       {'jwks_pairs': [{'jwks_url': 'https://another.example/keys'}]},
                       {'jwt_validation_pubkeys': ['another public key']},
                       {'oidc_discovery_ca_pem': ['custom trust root']}):
            with self.subTest(config=list(config)):
                from unittest.mock import Mock
                vault = Mock()
                vault.request.side_effect = [(200, {'data': {'github/': {'type': 'jwt'}}}),
                                             (200, {'data': config})]
                with self.assertRaisesRegex(SafeError, 'another trust configuration'):
                    configure(vault, 'owner/repo', 123, 456)
                self.assertTrue(all(call.args[0] == 'GET' for call in vault.request.call_args_list))


if __name__ == '__main__':
    unittest.main()

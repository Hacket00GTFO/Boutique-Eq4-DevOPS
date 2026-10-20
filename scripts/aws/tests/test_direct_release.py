"""Account boundaries, existing node recovery and application custody for direct AWS CD."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import direct_release as release
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'vault/tests'))
import test_delivery as fixtures
from bootstrap import seed_values
from common import SafeError


class Lab:
    def __init__(self, account=release.ACCOUNT, wrong_vpc=False, bad_state=False):
        self.account, self.wrong_vpc, self.bad_state = account, wrong_vpc, bad_state
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('sts', 'get-caller-identity'):
            return {'Account': self.account}
        if args[:2] == ('eks', 'describe-cluster'):
            return {'cluster': {'status': 'ACTIVE', 'resourcesVpcConfig': {'vpcId': 'vpc-owned'}}}
        if args[:2] == ('ecr', 'describe-repositories'):
            return {'repositories': [{'repositoryName': 'boutique-eq4/' + name} for name in release.SERVICES]}
        if args[:2] == ('eks', 'describe-nodegroup'):
            return {'nodegroup': {'resources': {'autoScalingGroups': [{'name': args[-1]}]}}}
        if args[0] == 'autoscaling':
            ident = 'i-11111111' if args[-1].endswith('-nodes') else 'i-22222222'
            return {'AutoScalingGroups': [{'AutoScalingGroupName': args[-1], 'Instances': [{'InstanceId': ident}]}]}
        if args[:2] == ('ec2', 'describe-instances'):
            return {'Reservations': [{'Instances': [
                {'InstanceId': 'i-11111111', 'VpcId': 'vpc-other' if self.wrong_vpc else 'vpc-owned',
                 'State': {'Name': 'terminated' if self.bad_state else 'stopped'}},
                {'InstanceId': 'i-22222222', 'VpcId': 'vpc-owned', 'State': {'Name': 'running'}},
            ]}]}
        return {}


class DirectReleaseTests(unittest.TestCase):
    def test_wrong_account_stops_before_any_resource_access(self):
        lab = Lab(account='123456789012')
        with self.assertRaisesRegex(SafeError, 'Wrong AWS account'):
            release.preflight(lab)
        self.assertEqual(lab.calls, [('sts', 'get-caller-identity')])

    def test_preflight_verifies_all_twelve_existing_repositories(self):
        lab = Lab()
        with contextlib.redirect_stdout(io.StringIO()):
            release.preflight(lab)
        query = next(call for call in lab.calls if call[0] == 'ecr')
        self.assertEqual(set(query[3:]), {'boutique-eq4/' + name for name in release.SERVICES})

    def test_revoked_credentials_diagnostic_suppresses_private_aws_output(self):
        for cause in ('voc-cancel-cred', 'ExpiredToken'):
            result = subprocess.CompletedProcess([], 1, '', cause + ' isolated-private-response')
            with self.subTest(cause=cause), patch.object(release.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(SafeError, 'three repository AWS secrets') as failure:
                    release.aws('eks', 'describe-cluster')
                self.assertNotIn('isolated-private-response', str(failure.exception))

    def test_missing_infrastructure_identifies_service_without_private_response(self):
        for service, code, expected in [('eks', 'ResourceNotFoundException', 'EKS cluster'),
                                        ('ecr', 'RepositoryNotFoundException', 'ECR repositories')]:
            result = subprocess.CompletedProcess([], 1, '', code + ' isolated-private-response')
            with self.subTest(service=service), patch.object(release.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(SafeError, expected) as failure:
                    release.aws(service, 'describe')
                self.assertNotIn('isolated-private-response', str(failure.exception))

    def test_resume_starts_only_stopped_members_of_the_existing_node_groups(self):
        lab = Lab()
        with contextlib.redirect_stdout(io.StringIO()):
            release.resume_nodes(lab)
        starts = [call for call in lab.calls if call[:2] == ('ec2', 'start-instances')]
        self.assertEqual(starts, [('ec2', 'start-instances', '--instance-ids', 'i-11111111')])
        self.assertFalse(any('ssm' in call or 'iam' in call for call in lab.calls))

    def test_wrong_vpc_or_terminated_nodes_fail_before_starting_instances(self):
        for settings in ({'wrong_vpc': True}, {'bad_state': True}):
            lab = Lab(**settings)
            with self.subTest(settings=settings), self.assertRaises(SafeError):
                release.resume_nodes(lab)
            self.assertFalse(any(call[:2] == ('ec2', 'start-instances') for call in lab.calls))

    def test_direct_transaction_preserves_the_existing_secret_without_vault(self):
        values = seed_values()
        cluster = fixtures.Cluster(values)
        output = io.StringIO()
        with patch.object(release.delivery, 'run', cluster.run), \
                patch.object(release.delivery, 'smoke') as smoke, \
                patch('common.Vault', side_effect=AssertionError('Vault must not be contacted')), \
                contextlib.redirect_stdout(output):
            release.deploy_existing(release.REGISTRY, 'a' * 40, '100.1', Lab())
        self.assertEqual(cluster.writes[0]['data'], cluster.previous['data'])
        smoke.assert_called_once_with(values)
        for value in values.values():
            self.assertNotIn(value, output.getvalue())

    def test_missing_original_secret_stops_before_helm_or_secret_writes(self):
        cluster = fixtures.Cluster(installed=False, pvcs=True)
        with patch.object(release.delivery, 'run', cluster.run), self.assertRaisesRegex(SafeError, 'custody'):
            release.deploy_existing(release.REGISTRY, 'a' * 40, '100.1', Lab())
        self.assertEqual(cluster.writes, [])
        self.assertFalse(any(command[0] == 'helm' for command in cluster.commands))

    def test_invalid_release_is_rejected_before_reading_private_application_values(self):
        lab = Lab()
        with patch.object(release.delivery, 'kubectl') as kubectl, self.assertRaises(SafeError):
            release.deploy_existing('another-registry', 'a' * 40, '100.1', lab)
        kubectl.assert_not_called()
        self.assertEqual(lab.calls, [])


if __name__ == '__main__':
    unittest.main()

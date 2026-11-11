import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from activate_actions import activate, validate_evidence, preserve_custody, AWS_KEYS
from common import SafeError
from recovery_evidence import REPO, ACCOUNT, require_recovery


def fixture():
    repository = {'id': 123, 'owner': {'id': 456}, 'permissions': {'admin': True}}
    run = {'repository': {'full_name': REPO}, 'id': 42, 'run_attempt': 2, 'head_sha': 'a' * 40,
           'head_branch': 'main', 'event': 'workflow_dispatch', 'path': '.github/workflows/ci-docker-ecr.yaml',
           'status': 'completed', 'conclusion': 'success'}
    jobs = [{'name': 'initialize-local-data', 'conclusion': 'success'}]
    variables = {'VAULT_INSTANCE_ID': 'i-12345678', 'VAULT_KMS_KEY_ARN': 'persistent-key', 'VAULT_SSM_BUCKET': 'ssm-bucket'}
    encrypted = b'isolated-encrypted-custody'
    record = {'version': 1, 'repo': REPO, 'repository_id': '123', 'owner_id': '456', 'account': ACCOUNT,
              'sha': 'a' * 40, 'run_id': '42', 'run_attempt': '2', 'instance_id': 'i-12345678',
              'kms_key_arn': 'persistent-key', 'ssm_bucket': 'ssm-bucket', 'dump_sha256': 'b' * 64,
              'custody_sha256': hashlib.sha256(encrypted).hexdigest(), 'pvc_uid': 'claim-1',
              'volume_name': 'pv-1', 'volume_handle': 'vol-1', 'table_counts': {'boutique.products': 9},
              **{key: True for key in ('kms_restart_verified', 'tls_verified', 'oidc_configured',
                                      'root_token_revoked', 'oidc_login_verified', 'application_values_verified',
                                      'restoration_verified', 'admin_env_verified')}}
    return repository, run, jobs, variables, record, encrypted


class ActivationEvidenceTests(unittest.TestCase):
    def test_other_run_attempt_branch_infrastructure_and_missing_checks_cannot_activate(self):
        repository, run, jobs, variables, record, encrypted = fixture()
        validate_evidence(REPO, repository, run, jobs, variables, record, encrypted)
        for field in ('oidc_login_verified', 'restoration_verified', 'root_token_revoked', 'run_attempt',
                      'run_id', 'sha', 'instance_id', 'kms_key_arn', 'account', 'custody_sha256'):
            invalid = dict(record, **{field: 'wrong'})
            with self.subTest(field=field), self.assertRaises(SafeError):
                validate_evidence(REPO, repository, run, jobs, variables, invalid, encrypted)
        for change in ({'head_branch': 'development'}, {'conclusion': 'failure'}, {'event': 'push'},
                       {'path': '.github/workflows/unrelated.yaml'}):
            with self.subTest(change=change), self.assertRaises(SafeError):
                validate_evidence(REPO, repository, dict(run, **change), jobs, variables, record, encrypted)
        with self.assertRaises(SafeError):
            validate_evidence(REPO, repository, run, [{'name': 'initialize-local-data', 'conclusion': 'skipped'}],
                              variables, record, encrypted)

    @unittest.skipUnless(os.name == 'posix', 'Private permanent custody requires POSIX')
    def test_activation_preserves_custody_before_removing_payload_and_enabling_flags(self):
        repository, run, jobs, variables, record, encrypted = fixture()
        events = []
        def api(repo, path='', method='GET'):
            if method != 'GET':
                events.append(('delete', path))
                return {}
            if not path:
                return repository
            if path == 'actions/runs/42':
                return run
            if '/jobs?' in path:
                return {'jobs': jobs}
            if '/variables?' in path:
                return {'variables': [{'name': k, 'value': v} for k, v in variables.items()]}
            if '/secrets?' in path:
                return {'secrets': [{'name': n} for n in (*AWS_KEYS, 'LOCAL_RECOVERY_PAYLOAD', 'VAULT_CI_TOKEN')]}
            return {}
        def download(args):
            directory = Path(args[-1])
            (directory / 'bootstrap-verified.json').write_text(json.dumps(record))
            (directory / 'vault-custody.tar.gpg').write_bytes(encrypted)
        with tempfile.TemporaryDirectory() as custody:
            def write(repo, kind, key, value, **kwargs):
                self.assertTrue((Path(custody) / 'actions-42-2/vault-custody.tar.gpg').exists())
                events.append(('set', key, value))
                variables[key] = value
            activate(REPO, 42, custody, api, download, write, lambda *_: None)
            self.assertEqual(events[-1], ('set', 'VAULT_AUTOMATION_READY', 'true'))
            self.assertIn(('set', 'DATA_RECOVERY_VERIFIED', 'true'), events)
            self.assertTrue(any(item[0] == 'delete' and item[1].endswith('LOCAL_RECOVERY_PAYLOAD') for item in events))
            self.assertEqual((Path(custody) / 'actions-42-2/vault-custody.tar.gpg').stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == 'posix', 'Private permanent custody requires POSIX')
    def test_existing_different_custody_is_never_overwritten(self):
        *_, record, encrypted = fixture()
        with tempfile.TemporaryDirectory() as directory:
            destination = preserve_custody(directory, record, encrypted)
            target = destination / 'vault-custody.tar.gpg'
            target.write_bytes(b'existing-other-backup')
            with self.assertRaises(SafeError):
                preserve_custody(directory, record, encrypted)
            self.assertEqual(target.read_bytes(), b'existing-other-backup')

    def test_invalid_evidence_cannot_mutate_github(self):
        repository, run, jobs, variables, record, encrypted = fixture()
        record['oidc_login_verified'] = False
        mutations = []
        def api(repo, path='', method='GET'):
            if method != 'GET':
                mutations.append(path)
            if not path:
                return repository
            if path == 'actions/runs/42':
                return run
            if '/jobs?' in path:
                return {'jobs': jobs}
            if '/variables?' in path:
                return {'variables': [{'name': k, 'value': v} for k, v in variables.items()]}
            if '/secrets?' in path:
                return {'secrets': [{'name': n} for n in AWS_KEYS]}
            return {}
        def download(args):
            directory = Path(args[-1])
            (directory / 'bootstrap-verified.json').write_text(json.dumps(record))
            (directory / 'vault-custody.tar.gpg').write_bytes(encrypted)
        write, prepare = Mock(), Mock()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(SafeError):
            activate(REPO, 42, directory, api, download, write, prepare)
        self.assertEqual(mutations, [])
        write.assert_not_called()
        prepare.assert_not_called()


class PersistentRecoveryTests(unittest.TestCase):
    def test_replaced_volume_or_missing_database_blocks_before_deploy(self):
        marker = {'data': {'dump_sha256': 'a' * 64, 'admin_env_verified': 'true', 'pvc_uid': 'claim-1',
                           'volume_name': 'pv-1', 'volume_handle': 'vol-1'}}
        pvc = {'metadata': {'uid': 'claim-1'}, 'status': {'phase': 'Bound'},
               'spec': {'storageClassName': 'boutique-ebs-gp3', 'volumeName': 'pv-1'}}
        pv = {'spec': {'claimRef': {'uid': 'claim-1'}, 'csi': {'driver': 'ebs.csi.aws.com', 'volumeHandle': 'vol-1'}}}
        values = [json.dumps(marker), json.dumps(pvc), json.dumps(pv), 'true\n']
        require_recovery(Mock(side_effect=values))
        wrong_pv = copy.deepcopy(pv)
        wrong_pv['spec']['csi']['volumeHandle'] = 'another-volume'
        for replies in ([json.dumps(marker), json.dumps(pvc), json.dumps(wrong_pv)], values[:3] + ['false']):
            with self.assertRaises(SafeError):
                require_recovery(Mock(side_effect=replies))


if __name__ == '__main__':
    unittest.main()

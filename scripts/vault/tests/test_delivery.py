"""Fail-closed validation and transaction recovery without cloud credentials."""
import ast
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import seed_values
from common import KEYS, SafeError, private_json, validate_values
from check_idempotence import check
import deploy

REGISTRY = '123456789012.dkr.ecr.us-east-1.amazonaws.com'
SHA = 'a' * 40


class Cluster:
    def __init__(self, values=None, installed=True, pvcs=False, fail_upgrade=False):
        self.previous = deploy.secret_manifest(values) if values else None
        self.installed, self.pvcs, self.fail_upgrade = installed, pvcs, fail_upgrade
        self.commands, self.writes = [], []
        self.storage = [{'metadata': {'name': 'boutique-ebs-gp3'},
                         'provisioner': 'ebs.csi.aws.com', 'volumeBindingMode': 'WaitForFirstConsumer'}]
        self.csi = True

    def run(self, args, payload=None, timeout=90):
        self.commands.append(args)
        words = ' '.join(args)
        if 'get storageclass' in words:
            return json.dumps({'items': self.storage})
        if 'get csidriver' in words:
            if not self.csi:
                raise SafeError('EBS CSI is not registered')
            return json.dumps({'metadata': {'name': 'ebs.csi.aws.com'}})
        if 'get secret' in words:
            return json.dumps(self.previous) if self.previous else ''
        if 'get configmap local-recovery-verified' in words:
            return json.dumps({'data': {'dump_sha256': 'a' * 64, 'admin_env_verified': 'true',
                                       'pvc_uid': 'claim-1', 'volume_name': 'pv-1', 'volume_handle': 'vol-1'}})
        if 'get pvc data-postgres-0' in words:
            return json.dumps({'metadata': {'uid': 'claim-1'}, 'status': {'phase': 'Bound'},
                               'spec': {'storageClassName': 'boutique-ebs-gp3', 'volumeName': 'pv-1'}})
        if 'get pv pv-1' in words:
            return json.dumps({'spec': {'claimRef': {'uid': 'claim-1'},
                                       'csi': {'driver': 'ebs.csi.aws.com', 'volumeHandle': 'vol-1'}}})
        if 'psql' in args:
            return 'true\n'
        if 'get pvc' in words:
            return json.dumps({'items': [{'metadata': {'name': 'data'}}] if self.pvcs else []})
        if args[:2] == ['helm', 'list']:
            return json.dumps([{'name': deploy.RELEASE, 'revision': '2', 'status': 'deployed'}] if self.installed else [])
        if 'apply' in args:
            self.writes.append(json.loads(payload))
        if args[:2] == ['helm', 'upgrade'] and self.fail_upgrade:
            raise SafeError('Injected upgrade failure')
        return ''


class DeliveryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Custody permissions require Linux/WSL')
    def test_custody_is_private_exclusive_and_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'operator.json'
            private_json(target, {'client_token': 'isolated-test-fixture'})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                private_json(target, {})
            Path(directory).chmod(0o755)
            try:
                with self.assertRaises(SafeError):
                    private_json(Path(directory) / 'unsafe.json', {})
            finally:
                Path(directory).chmod(0o700)

    def test_idempotence_requires_a_successful_zero_change_recap(self):
        self.assertTrue(check('i-123 : ok=20 changed=0 unreachable=0 failed=0 skipped=0'))
        for log in ('', 'i-123 : ok=20 changed=1 unreachable=0 failed=0',
                    'i-123 : ok=0 changed=0 unreachable=1 failed=0',
                    'i-123 : ok=20 changed=0 unreachable=0 failed=1'):
            self.assertFalse(check(log))
    def test_generated_seed_is_consistent_and_unique(self):
        one, two = seed_values(), seed_values()
        self.assertEqual(set(one), set(KEYS))
        self.assertNotEqual(one['POSTGRES_PASSWORD'], two['POSTGRES_PASSWORD'])
        self.assertEqual(validate_values(one), one)

    def test_missing_or_invalid_values_fail_before_any_cluster_access(self):
        values = seed_values()
        cases = [dict(values, ADMIN_API_TOKEN=''), dict(values, ADMIN_PASSWORD='short'),
                 dict(values, DATABASE_URL='postgresql://user:secret@external/boutique'),
                 dict(values, CHECKOUT_API_TOKEN='x' * 30 + '\n'), dict(values, ADMIN_EMAIL=None)]
        for invalid in cases:
            with self.subTest(), patch.object(deploy, 'run') as run:
                with self.assertRaises(SafeError):
                    deploy.deploy(invalid, REGISTRY, SHA, '1.1')
                run.assert_not_called()

    def test_urls_cannot_disagree_with_passwords(self):
        values = seed_values()
        values['APP_DB_PASSWORD'] = 'another-password-with-enough-length'
        with self.assertRaises(SafeError):
            validate_values(values)

    def test_unbuilt_or_invalid_release_is_rejected(self):
        for registry, sha, revision in [('evil.example', SHA, '1.1'), (REGISTRY, 'main', '1.1'), (REGISTRY, SHA, 'x,y')]:
            with self.subTest(), patch.object(deploy, 'run') as run:
                with self.assertRaises(SafeError):
                    deploy.deploy(seed_values(), registry, sha, revision)
                run.assert_not_called()

    def test_database_and_bootstrap_rotation_is_blocked_before_write(self):
        old = seed_values()
        for key in ('APP_DB_PASSWORD', 'ADMIN_PASSWORD', 'ADMIN_EMAIL'):
            values = dict(old)
            values[key] += 'changed'
            cluster = Cluster(old)
            with self.subTest(key=key), patch.object(deploy, 'run', cluster.run):
                with self.assertRaises(SafeError):
                    deploy.deploy(values, REGISTRY, SHA, '1.1')
                self.assertEqual(cluster.writes, [])

    def test_existing_pvc_without_secret_is_never_rekeyed(self):
        cluster = Cluster(installed=False, pvcs=True)
        with patch.object(deploy, 'run', cluster.run), self.assertRaises(SafeError):
            deploy.deploy(seed_values(), REGISTRY, SHA, '1.1')
        self.assertEqual(cluster.writes, [])

    def test_api_rotation_applies_only_to_lab_and_runs_smoke(self):
        old = seed_values()
        new = dict(old, ADMIN_API_TOKEN='new-api-token-' * 4)
        cluster = Cluster(old)
        with patch.object(deploy, 'run', cluster.run), patch.object(deploy, 'smoke') as smoke:
            deploy.deploy(new, REGISTRY, SHA, '123.2')
        smoke.assert_called_once_with(new)
        self.assertEqual(cluster.writes[0]['metadata']['namespace'], 'boutique-vault-lab')
        self.assertEqual(base64.b64decode(cluster.writes[0]['data']['ADMIN_API_TOKEN']).decode(), new['ADMIN_API_TOKEN'])
        upgrade = next(a for a in cluster.commands if a[:2] == ['helm', 'upgrade'])
        self.assertIn('postgresIntegration.rolloutRevision=123.2', upgrade)
        self.assertIn('postgresIntegration.database.storageClassName=boutique-ebs-gp3', upgrade)
        self.assertNotIn(new['ADMIN_API_TOKEN'], ' '.join(' '.join(c) for c in cluster.commands))

    def test_default_class_does_not_replace_the_explicit_lab_class(self):
        cluster = Cluster(installed=False)
        cluster.storage = [{'metadata': {'name': 'gp2', 'annotations': {
            'storageclass.kubernetes.io/is-default-class': 'true'}},
            'provisioner': 'kubernetes.io/aws-ebs'}]
        with patch.object(deploy, 'run', cluster.run), self.assertRaisesRegex(SafeError, 'boutique-ebs-gp3'):
            deploy.deploy(seed_values(), REGISTRY, SHA, '1.1')
        self.assertEqual(cluster.writes, [])

    def test_incompatible_lab_storage_fails_before_writing_secrets(self):
        for change in ({'provisioner': 'other.driver'}, {'volumeBindingMode': 'Immediate'}):
            cluster = Cluster(installed=False)
            cluster.storage[0].update(change)
            with self.subTest(change=change), patch.object(deploy, 'run', cluster.run), self.assertRaises(SafeError):
                deploy.deploy(seed_values(), REGISTRY, SHA, '1.1')
            self.assertEqual(cluster.writes, [])

    def test_missing_ebs_csi_fails_before_writing_secrets(self):
        cluster = Cluster(installed=False)
        cluster.csi = False
        with patch.object(deploy, 'run', cluster.run), self.assertRaisesRegex(SafeError, 'CSI'):
            deploy.deploy(seed_values(), REGISTRY, SHA, '1.1')
        self.assertEqual(cluster.writes, [])

    def test_helm_failure_restores_secret_and_recovers_consumers(self):
        old = seed_values()
        cluster = Cluster(old, fail_upgrade=True)
        with patch.object(deploy, 'run', cluster.run), patch.object(deploy, 'smoke') as smoke:
            with self.assertRaises(SafeError):
                deploy.deploy(dict(old, ADMIN_API_TOKEN='new-value-' * 5), REGISTRY, SHA, '1.2')
        self.assertEqual(cluster.writes[-1]['data'], deploy.secret_manifest(old)['data'])
        self.assertTrue(any(a[:2] == ['helm', 'rollback'] for a in cluster.commands))
        self.assertEqual(sum('restart' in a for a in cluster.commands), 4)
        smoke.assert_called_once_with(old)

    def test_smoke_failure_is_a_deployment_failure_with_rollback(self):
        old = seed_values()
        cluster = Cluster(old)
        with patch.object(deploy, 'run', cluster.run), patch.object(deploy, 'smoke', side_effect=[SafeError('smoke failed'), None]):
            with self.assertRaises(SafeError):
                deploy.deploy(dict(old, CATALOG_READ_TOKEN='new-catalog-' * 4), REGISTRY, SHA, '1.2')
        self.assertEqual(cluster.writes[-1]['data'], deploy.secret_manifest(old)['data'])

    def test_failed_first_install_preserves_secret_when_pvc_exists(self):
        cluster = Cluster(installed=False, fail_upgrade=True)
        original = cluster.run
        def run(args, payload=None, timeout=90):
            if args[:2] == ['helm', 'upgrade']:
                cluster.pvcs = True
            return original(args, payload, timeout)
        with patch.object(deploy, 'run', run), self.assertRaises(SafeError):
            deploy.deploy(seed_values(), REGISTRY, SHA, '1.1')
        self.assertFalse(any('delete' in a for a in cluster.commands))

    def test_initial_smoke_failure_removes_release_but_preserves_database_custody(self):
        cluster = Cluster(installed=False)
        def fail_smoke(_):
            cluster.pvcs = True
            raise SafeError('Initial smoke failed')
        with patch.object(deploy, 'run', cluster.run), patch.object(deploy, 'smoke', fail_smoke), self.assertRaises(SafeError):
            deploy.deploy(seed_values(), REGISTRY, SHA, '1.1')
        self.assertTrue(any(a[:2] == ['helm', 'uninstall'] for a in cluster.commands))
        self.assertFalse(any('delete' in a for a in cluster.commands))

    def test_smoke_never_receives_database_superuser_credentials(self):
        values = seed_values()
        with patch.object(deploy, 'kubectl', return_value='{"passed": 30}') as kubectl:
            deploy.smoke(values)
        payload = json.loads(kubectl.call_args.kwargs['payload'])
        self.assertEqual(set(payload['env']), {'ADMIN_EMAIL', 'ADMIN_PASSWORD', 'ADMIN_API_TOKEN', 'STOREFRONT_TEST_URL'})
        self.assertEqual(payload['env']['STOREFRONT_TEST_URL'], 'http://frontend:80')
        self.assertNotIn(values['POSTGRES_PASSWORD'], json.dumps(payload))

    def test_functional_http_probe_uses_the_deployment_service_port(self):
        # Exercise the actual suite's HTTP request function without running its mutations.
        source = Path(deploy.__file__).resolve().parents[1] / 'test-storefront-integration.py'
        request_function = next(node for node in ast.parse(source.read_text(encoding='utf-8')).body
                                if isinstance(node, ast.FunctionDef) and node.name == 'request')
        client = Mock()
        namespace = {'os': os, 'json': json, 'urllib': urllib, 'client': client, 'jar': []}
        exec(compile(ast.Module(body=[request_function], type_ignores=[]), str(source), 'exec'), namespace)
        for configured, expected in ((None, 'http://frontend:8080'), ('http://frontend:80', 'http://frontend:80')):
            with self.subTest(configured=configured), patch.dict(os.environ):
                os.environ.pop('STOREFRONT_TEST_URL', None)
                if configured:
                    os.environ['STOREFRONT_TEST_URL'] = configured
                response = io.BytesIO(b'healthy')
                response.code, response.url = 200, expected + '/_readyz'
                client.open.return_value = response
                namespace['request']('/_readyz')
                self.assertEqual(client.open.call_args.args[0].full_url, expected + '/_readyz')

    def test_restore_drops_stale_identity_fields(self):
        previous = deploy.secret_manifest(seed_values())
        previous['metadata'].update(resourceVersion='old', uid='old', managedFields=[{}])
        self.assertEqual(set(deploy.restore_manifest(previous)['metadata']), {'name', 'namespace'})

    def test_command_failure_does_not_log_secrets(self):
        from subprocess import CompletedProcess
        sentinel = 'credential-that-must-not-appear'
        with patch.object(deploy.subprocess, 'run', return_value=CompletedProcess([], 1, sentinel, sentinel)):
            with self.assertRaises(SafeError) as failure:
                deploy.run(['kubectl', 'apply'])
        self.assertNotIn(sentinel, str(failure.exception))

    def test_recovery_failure_is_explicit_without_sensitive_values(self):
        old = seed_values()
        cluster = Cluster(old, fail_upgrade=True)
        stderr = io.StringIO()
        with (patch.object(deploy, 'run', cluster.run),
              patch.object(deploy, 'smoke', side_effect=SafeError('private error')),
              contextlib.redirect_stderr(stderr)):
            with self.assertRaises(SafeError):
                deploy.deploy(old, REGISTRY, SHA, '1.1')
        self.assertIn('RECOVERY REQUIRED', stderr.getvalue())
        self.assertNotIn(old['ADMIN_PASSWORD'], stderr.getvalue())


if __name__ == '__main__':
    unittest.main()

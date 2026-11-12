"""Real TLS/KV/ACL/expiration/seal tests against an isolated Vault subprocess."""
import base64
import contextlib
import gzip
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import socket
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DATA_PATH, SafeError, Vault, validate_values
import bootstrap
import recover_root
import repair_github_oidc


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.environ.get('VAULT_TEST_BINARY'), 'Set VAULT_TEST_BINARY to run a real isolated Vault')
class LiveVaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import ipaddress
        cls.temp = tempfile.TemporaryDirectory(prefix='boutique-vault-test-')
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        (root / 'data').mkdir()
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .sign(private, hashes.SHA256()))
        (root / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root / 'key.pem').write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cls.port = free_port()
        listener = {'address': f'127.0.0.1:{cls.port}', 'cluster_address': f'127.0.0.1:{free_port()}',
                    'tls_cert_file': str(root / 'cert.pem'), 'tls_key_file': str(root / 'key.pem')}
        config = {'ui': True, 'disable_mlock': True, 'api_addr': f'https://127.0.0.1:{cls.port}',
                  'cluster_addr': 'https://' + listener['cluster_address'],
                  'storage': {'raft': {'path': str(root / 'data'), 'node_id': 'test'}}, 'listener': {'tcp': listener}}
        cls.config_path = root / 'config.json'
        cls.config_path.write_text(json.dumps(config))
        cls.log = (root / 'server.log').open('w')
        cls.addClassCleanup(cls.log.close)
        cls.command = [os.environ['VAULT_TEST_BINARY'], 'server', '-config=' + str(root / 'config.json')]
        cls.process = subprocess.Popen(cls.command,
                                       stdout=cls.log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        cls.addClassCleanup(cls.stop)
        cls.env_patch = patch.dict(os.environ, {'VAULT_ADDR': config['api_addr'], 'VAULT_CACERT': str(root / 'cert.pem')})
        cls.env_patch.start()
        cls.addClassCleanup(cls.env_patch.stop)
        cls.root = Vault()
        for _ in range(80):
            try:
                cls.root.request('GET', 'sys/init')
                break
            except SafeError:
                if cls.process.poll() is not None:
                    raise RuntimeError('Isolated Vault exited; inspect the temporary test configuration.')
                time.sleep(0.25)
        _, init = cls.root.request('PUT', 'sys/init', {'secret_shares': 3, 'secret_threshold': 2})
        cls.shares = init['keys_base64']
        cls.root.token = init['root_token']
        for share in cls.shares[:2]:
            cls.root.request('PUT', 'sys/unseal', {'key': share})
        for _ in range(40):
            try:
                cls.root.ready()
                break
            except SafeError:
                time.sleep(0.1)
        # The audit device uses a portable temp path in tests; production config enables /var/log/vault.
        cls.root.request('PUT', 'sys/audit/file', {'type': 'file', 'options': {'file_path': str(root / 'audit.json'), 'log_raw': 'false'}})
        with patch.dict(os.environ, {'VAULT_TOKEN': cls.root.token}), patch.object(sys, 'argv', ['bootstrap.py', 'configure']), contextlib.redirect_stdout(io.StringIO()):
            bootstrap.main()
        cls.values = bootstrap.seed_values()
        cls.root.request('POST', DATA_PATH, {'options': {'cas': 0}, 'data': cls.values})

    @classmethod
    def stop(cls):
        cls.process.terminate()
        cls.process.wait(timeout=15)

    def token(self, ttl='4h'):
        result = self.root.request('POST', 'auth/token/create/boutique-ci', {
            'policies': ['boutique-ci'], 'no_default_policy': True, 'ttl': ttl,
            'explicit_max_ttl': ttl, 'renewable': False})[1]['auth']
        return Vault(result['client_token']), result

    def test_ci_reads_exact_path_and_cannot_write_or_administer(self):
        client, token = self.token()
        self.assertEqual(token['policies'], ['boutique-ci'])
        self.assertFalse(token['renewable'])
        self.assertLessEqual(token['lease_duration'], 14400)
        self.assertEqual(validate_values(client.request('GET', DATA_PATH)[1]['data']['data']), self.values)
        for method, path, body in [('POST', DATA_PATH, {'data': {}}), ('GET', 'sys/audit', None),
                                   ('GET', 'secret/data/other', None), ('GET', 'secret/metadata/boutique/lab/integration', None)]:
            with self.subTest(path=path), self.assertRaisesRegex(SafeError, '403'):
                client.request(method, path, body)

    @unittest.skipUnless(os.name == 'posix', 'Operator custody files require POSIX permissions')
    def test_operator_survives_root_revocation_and_can_issue_restricted_ci(self):
        _, result = self.root.request('POST', 'auth/token/create-orphan', {
            'policies': ['root'], 'display_name': 'bootstrap-test',
        })
        bootstrap_root = Vault(result['auth']['client_token'])
        operator = ci = None
        try:
            with tempfile.TemporaryDirectory(prefix='boutique-operator-test-') as directory:
                output = Path(directory) / 'operator.json'
                with patch.dict(os.environ, {'VAULT_TOKEN': bootstrap_root.token}), \
                     patch.object(sys, 'argv', ['bootstrap.py', 'issue-operator', '--output', str(output)]), \
                     contextlib.redirect_stdout(io.StringIO()):
                    bootstrap.main()
                auth = json.loads(output.read_text())
                self.assertTrue(auth['orphan'])
                self.assertFalse(auth['renewable'])
                self.assertLessEqual(auth['lease_duration'], 86400)
                self.assertIn('boutique-operator', auth['policies'])
                self.assertNotIn('root', auth['policies'])
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                operator = Vault(auth['client_token'])
                bootstrap_root.request('POST', 'auth/token/revoke-self')
                bootstrap_root = None
                self.assertEqual(operator.request('GET', DATA_PATH)[1]['data']['data'], self.values)
                # Reading recovery status must be possible without opening unauthenticated access.
                operator.request('GET', 'sys/generate-root/attempt')
                _, capabilities = operator.request('POST', 'sys/capabilities-self',
                                                   {'paths': ['sys/rekey/init', 'sys/rekey/update', 'sys/rekey/verify']})
                for path in ('sys/rekey/init', 'sys/rekey/update', 'sys/rekey/verify'):
                    self.assertIn('update', capabilities[path])
                    self.assertIn('sudo', capabilities[path])
                _, issued = operator.request('POST', 'auth/token/create/boutique-ci', {
                    'policies': ['boutique-ci'], 'no_default_policy': True,
                    'ttl': '1m', 'explicit_max_ttl': '1m', 'renewable': False,
                })
                ci = Vault(issued['auth']['client_token'])
                self.assertEqual(issued['auth']['policies'], ['boutique-ci'])
                self.assertTrue(issued['auth']['orphan'])
                self.assertEqual(ci.request('GET', DATA_PATH)[1]['data']['data'], self.values)
                with self.assertRaisesRegex(SafeError, '403'):
                    ci.request('GET', 'sys/generate-root/attempt')
        finally:
            if ci:
                self.root.request('POST', 'auth/token/revoke', {'token': ci.token})
            if operator:
                self.root.request('POST', 'auth/token/revoke', {'token': operator.token})
            if bootstrap_root:
                bootstrap_root.request('POST', 'auth/token/revoke-self')

    def test_expired_token_fails_closed(self):
        client, _ = self.token('1s')
        time.sleep(1.3)
        with self.assertRaisesRegex(SafeError, '403'):
            client.request('GET', DATA_PATH)

    def test_revoked_token_fails_closed(self):
        client, _ = self.token()
        self.root.request('POST', 'auth/token/revoke', {'token': client.token})
        with self.assertRaisesRegex(SafeError, '403'):
            client.request('GET', DATA_PATH)

    def test_policy_denial_and_recovery(self):
        client, _ = self.token()
        try:
            self.root.request('PUT', 'sys/policies/acl/boutique-ci', {'policy': f'path "{DATA_PATH}" {{ capabilities = ["deny"] }}'})
            with self.assertRaisesRegex(SafeError, '403'):
                client.request('GET', DATA_PATH)
        finally:
            self.root.request('PUT', 'sys/policies/acl/boutique-ci', {'policy': (bootstrap.POLICIES / 'boutique-ci.hcl').read_text()})
        self.assertIn('DATABASE_URL', client.request('GET', DATA_PATH)[1]['data']['data'])

    def test_sealed_vault_fails_and_recovers_with_same_data(self):
        try:
            self.root.request('PUT', 'sys/seal')
            with self.assertRaises(SafeError):
                self.root.ready()
            with self.assertRaisesRegex(SafeError, '503'):
                self.root.request('GET', DATA_PATH)
        finally:
            for share in self.shares[:2]:
                self.root.request('PUT', 'sys/unseal', {'key': share})
            for _ in range(40):
                try:
                    self.root.ready()
                    break
                except SafeError:
                    time.sleep(0.1)
        self.assertEqual(self.root.request('GET', DATA_PATH)[1]['data']['data'], self.values)

    def test_untrusted_certificate_is_rejected(self):
        client = Vault(self.root.token)
        client.context = ssl.create_default_context()
        with self.assertRaises(SafeError):
            client.request('GET', 'sys/init')
        with patch.dict(os.environ, {'VAULT_CACERT': ''}):
            with self.assertRaises(SafeError):
                Vault()

    def test_http_is_rejected(self):
        with patch.dict(os.environ, {'VAULT_ADDR': 'http://localhost:8200'}):
            with self.assertRaises(SafeError):
                Vault()

    def test_operations_collector_reads_public_health_without_exposing_tokens(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('operations_report_live_test', Path(__file__).resolve().parents[2] / 'operations_report.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.dict(os.environ, {'VAULT_TOKEN': 'PRIVATE_COLLECTOR_SENTINEL'}):
            report = module.vault_report()
        self.assertTrue(report['report']['reachable'])
        self.assertTrue(report['report']['initialized'])
        self.assertFalse(report['report']['sealed'])
        self.assertNotIn('PRIVATE_COLLECTOR_SENTINEL', json.dumps(report))
        self.assertEqual(set(report['report']), {'reachable', 'initialized', 'sealed', 'standby', 'version', 'http_status'})

    def test_ui_is_served_over_verified_https(self):
        # Exercise the browser's gzip transport, including complete body decoding.
        request = urllib.request.Request(self.root.url + '/ui/', headers={'Accept-Encoding': 'gzip'})
        with urllib.request.urlopen(request, context=self.root.context, timeout=15) as response:
            self.assertEqual(response.status, 200)
            body = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            self.assertIn('Vault', body.decode())

    def test_audit_does_not_contain_plaintext_application_secrets(self):
        self.root.request('GET', DATA_PATH)
        audit = (Path(self.temp.name) / 'audit.json').read_text()
        self.assertIn('secret/data/boutique/lab/integration', audit)
        self.assertNotIn(self.values['POSTGRES_PASSWORD'], audit)
        self.assertNotIn(self.values['ADMIN_API_TOKEN'], audit)

    def test_seed_refuses_existing_values_and_configure_is_repeatable(self):
        with patch.dict(os.environ, {'VAULT_TOKEN': self.root.token}), contextlib.redirect_stdout(io.StringIO()):
            with patch.object(sys, 'argv', ['bootstrap.py', 'configure']):
                bootstrap.main()
            with patch.object(sys, 'argv', ['bootstrap.py', 'seed']), self.assertRaises(SafeError):
                bootstrap.main()
        self.assertEqual(self.root.request('GET', DATA_PATH)[1]['data']['data'], self.values)

    @unittest.skipUnless(os.name == 'posix' and shutil.which('gpg'), 'Requires POSIX and GPG')
    def test_root_recovery_quorum_preserves_data_and_closes_access(self):
        custody = Path(self.temp.name) / 'recovery-custody'
        custody.mkdir(mode=0o700)
        home = custody / 'gnupg'
        home.mkdir(mode=0o700)
        gpg = ['gpg', '--homedir', str(home)]
        original_config = self.config_path.read_text()
        recovered = None

        def toggle(enabled):
            config = json.loads(original_config)
            config['enable_unauthenticated_access'] = ['generate-root'] if enabled else []
            self.config_path.write_text(json.dumps(config))
            self.process.send_signal(signal.SIGHUP)

        try:
            for name in ('custodian2', 'custodian3'):
                # Empty passphrases are only for ephemeral test keys in this isolated fixture.
                subprocess.run(gpg + ['--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                                     '--quick-generate-key', name + '@recovery.invalid',
                                     'default', 'default', '0'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            recipient = custody / 'custodian2.pub'
            recipient.write_bytes(subprocess.run(gpg + ['--export', 'custodian2@recovery.invalid'],
                                                 stdout=subprocess.PIPE, check=True).stdout)
            encrypted_shares = []
            for index, share in enumerate(self.shares):
                name = 'custodian3' if index == 2 else 'custodian2'
                result = subprocess.run(gpg + ['--batch', '--trust-model', 'always', '--recipient',
                                               name + '@recovery.invalid', '--encrypt'],
                                        input=share.encode(), stdout=subprocess.PIPE, check=True)
                encrypted_shares.append(base64.b64encode(result.stdout).decode())
            init_file = custody / 'init.json'
            init_file.write_text(json.dumps({'keys_base64': encrypted_shares}))
            output = custody / 'recovered-root.json'
            messages = io.StringIO()
            with contextlib.redirect_stdout(messages):
                recover_root.recover(Vault(), init_file, [2, 3], recipient, output, home, toggle)
            document = json.loads(output.read_text())
            self.assertEqual(document['status'], 'verified')
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            token = recover_root.decrypt_pgp(base64.b64decode(document['root_token']), home)
            self.assertNotIn(token, output.read_text())
            self.assertNotIn(token, messages.getvalue())
            self.assertNotIn(self.root.token, messages.getvalue())
            recovered = Vault(token)
            self.assertIn('root', recovered.request('GET', 'auth/token/lookup-self')[1]['data']['policies'])
            self.assertEqual(Vault().request('GET', 'sys/generate-root/attempt', allowed=(403,))[0], 403)
            self.root.ready()
            self.assertEqual(self.root.request('GET', DATA_PATH)[1]['data']['data'], self.values)
        finally:
            if recovered:
                recovered.request('POST', 'auth/token/revoke-self')
            self.config_path.write_text(original_config)
            self.process.send_signal(signal.SIGHUP)
            subprocess.run(['gpgconf', '--homedir', str(home), '--kill', 'gpg-agent'], check=False)

    def test_oidc_repair_failure_revokes_real_root_and_operator_tokens(self):
        auth = self.root.request('POST', 'auth/token/create-orphan', {'policies': ['root']})[1]['auth']
        temporary_root = Vault(auth['client_token'])
        operator_tokens = []
        def failing_configuration(operator, *args):
            operator_tokens.append(operator.token)
            self.assertEqual(operator.request('GET', 'auth/token/lookup-self')[1]['data']['policies'], ['boutique-operator'])
            with self.assertRaisesRegex(SafeError, 'HTTP 403'):
                temporary_root.request('GET', 'auth/token/lookup-self')
            raise SafeError('Simulated incompatible role')
        with patch.object(repair_github_oidc, 'configure', side_effect=failing_configuration), self.assertRaises(SafeError):
            repair_github_oidc.repair(temporary_root, 'owner/repo', 123, 456)
        self.assertEqual(len(operator_tokens), 1)
        for token in [temporary_root.token, *operator_tokens]:
            with self.assertRaisesRegex(SafeError, 'HTTP 403'):
                Vault(token).request('GET', 'auth/token/lookup-self')
        self.root.ready()

    def test_z_restart_retains_raft_and_requires_unseal(self):
        self.__class__.stop()
        self.__class__.process = subprocess.Popen(self.command, stdout=self.log, stderr=subprocess.STDOUT,
                                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        for _ in range(80):
            try:
                if self.root.request('GET', 'sys/init')[1].get('initialized'):
                    break
            except SafeError:
                pass
            time.sleep(0.25)
        with self.assertRaises(SafeError):
            self.root.ready()
        for share in self.shares[:2]:
            self.root.request('PUT', 'sys/unseal', {'key': share})
        for _ in range(40):
            try:
                self.root.ready()
                break
            except SafeError:
                time.sleep(0.1)
        self.assertEqual(self.root.request('GET', DATA_PATH)[1]['data']['data'], self.values)


if __name__ == '__main__':
    unittest.main()
